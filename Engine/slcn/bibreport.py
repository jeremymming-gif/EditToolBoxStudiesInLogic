r"""参考文献核对**报告**：把一份 .bib 文本变成人可读的四节 markdown。

为什么单独一个模块（2026-10-15）
--------------------------------
同一份报告现在有**两个消费者**，而它们必须给出**同一种**报告：

  · `checkbib.py` —— 编辑交回的 .bib 的离线体检（CLI，退出码 0/1/2）；
  · `convert.py`  —— 把 AI 结果并入 `reference.bib` **之后**立刻自检，
    写 `<stem>_回读校验报告.md` 并把「结论」一行打进控制台（用户 2026-10-15 的决定：
    AI 结果要进产物，就必须当场有一份离线体检报告兜着）。

两条路若各写一份报告，判据迟早分叉（一边报了、另一边没报，编辑就不知道该信谁）。
所以报告本体在这里，`checkbib.py` 退化成「读文件 → build_report → 打印/落盘」的薄壳，
`convert.py` 直接拿内存里的 bib 字符串调同一个 `build_report()`。

硬性约束
--------
  · **不 import `slcn.llm`**、不联网、不调任何模型（核实文献真伪是编辑的事）；
  · 值层面的规则一律复用 `slcn.bibcheck` / `slcn.bibnorm`，解析一律复用
    `slcn.bibparse`，**不另写一套**。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from . import bibcheck, bibnorm, report_header as _rh
from .bibparse import LEVEL_INFO, LEVEL_PROBLEM, Entry, Finding, _clip, parse_entries

SECTION_ORDER = ("syntax", "spec", "cite", "diff")
SECTION_TITLES = {
    "syntax": "一、可解析性（语法）",
    "spec": "二、编辑部规范",
    "cite": "三、引用键闭合（正文 ↔ bib）",
    "diff": "四、与 --original 的差异",
}

# 任务书第三节的固定字段顺序（只查「出现顺序」的相对关系）
SPEC_FIELD_ORDER = (
    "author", "title", "journal", "booktitle", "editor", "publisher", "address",
    "edition", "volume", "number", "pages", "year", "userf",
)

# ── 任务书第三节的条号：**唯一来源**（第 78 条）────────────────────────────
#
# 权威文本是 `slcn/refmark.py` 的 `FORMAT_MD` —— 它渲染成任务书
# 「## 三、请交付**完整、可直接编译的 .bib 文本**」那张编号清单。
#
# 2026-10-15 前，本文件把条号**硬写**在消息里，而且**整体错一位**：
#   · 「待核实注释」写成第 5 条（实际第 6 条）；
#   · 「不确定的值不要猜」写成第 6 条（实际第 7 条）；
#   · 「每条写全所有已知字段」写成第 2 条（实际第 3 条）；
#   · 「类型已改注释」写成第 7 条（实际第 8 条）。
# 编辑照报告里的 5 去翻任务书，翻到的是「页码用 `--`；期号去前导零」——**指错路**。
#
# 现在只在这里定义一次，使用处一律 `clause_no(...)`。表里第二项是**锚点**：
# 该条正文里必须原样出现这句话；`tests/test_bibreport_note_and_clause.py` 会把下面的
# 编号与 `refmark.FORMAT_MD` 里实际渲染的编号**逐条对齐**——任务书改号、或这里写错，
# 那个测试立刻变红。
SECTION3_CLAUSES: dict[str, tuple[int, str]] = {
    "key_unchanged": (1, "key 一律不改"),
    "author_invert": (2, "英文作者 / 编者一律倒装"),
    "all_known_fields": (3, "每条写全所有已知字段"),
    "userf_cn": (4, "userf = {cn}"),
    "pages_number_month": (5, "页码用 `--`"),
    "missing_field_note": (6, "查不到的字段就省略该字段"),
    "no_guessing": (7, "不确定的值不要猜"),
    "type_changed": (8, "类型已改"),
    "source_note": (11, "来源标注"),
}


def clause_no(name: str) -> int:
    """任务书第三节第 N 条的 N（唯一来源见 `SECTION3_CLAUSES`，第 78 条）。"""
    return SECTION3_CLAUSES[name][0]


# 每条最多显示多少条 bullet（附录里逐条全列）
BULLET_LIMIT = 80

# 报告末尾那句「谁生成的」。默认文案**一字不改**（`checkbib.py` 的 86 项断言里
# 就有它），`convert.py` 生成合并后的报告时换成自己的说法（见 build_report 的 source_note）。
SOURCE_NOTE_CHECKBIB = (
    "> 由 `checkbib.py` **离线**生成（不联网、不调用任何模型）；规范规则复用 "
    "`slcn/bibcheck.py`（必填字段、引用抽取）与 `slcn/bibnorm.py`（页码/期号/月份/userf、DROP_FIELDS）。"
)
SOURCE_NOTE_MERGE = (
    "> 由 `convert.py` 在**并入 AI 结果之后**离线生成（不联网、不调用任何模型）；"
    "规范规则复用 `slcn/bibcheck.py`（必填字段、引用抽取）与 `slcn/bibnorm.py`"
    "（页码/期号/月份/userf、DROP_FIELDS）。"
)


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class DiffRow:
    """一个键在「原件 → 退回版」里的变化。kind: same/changed/renamed/deleted/added"""

    key: str
    kind: str
    otype: str = ""
    ntype: str = ""
    old_key: str = ""
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Report:
    returned: str = ""
    original: str = ""
    tex: str = ""
    total: int = 0
    entries: list[Entry] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cited: set[str] = field(default_factory=set)
    n_cite_cmds: int = 0
    diff: list[DiffRow] = field(default_factory=list)
    #: diff 匹配统计（P1-1/P2-5）：{"key": N, "title": M, "degraded": bool, "overlap": x}
    match_stats: dict = field(default_factory=dict)

    def add(self, key: str, section: str, level: str, detail: str) -> None:
        self.findings.append(Finding(key, section, level, detail))

    @property
    def n_problem(self) -> int:
        return sum(1 for f in self.findings if f.level == LEVEL_PROBLEM)

    @property
    def n_info(self) -> int:
        return sum(1 for f in self.findings if f.level == LEVEL_INFO)

    @property
    def ok(self) -> bool:
        return self.n_problem == 0


# ── ② 编辑部规范 ─────────────────────────────────────────────────────────


def _bibcheck_field_names() -> frozenset[str]:
    """`slcn/bibcheck.py` 字段表（`REQUIRED`）里出现过的**所有**字段名（must + nice）。

    第 77 条：报告要在「待核实」注释里认字段名，**只能**用这张表 —— bibcheck 加了
    字段这里就自动跟上，**不许另列一份**（两张表迟早分叉，那正是本缺陷的成因之一）。
    """
    names: set[str] = set()
    for spec in bibcheck.REQUIRED.values():
        names.update(spec.get("must", ()))
        names.update(spec.get("nice", ()))
    return frozenset(names)


KNOWN_FIELD_NAMES: frozenset[str] = _bibcheck_field_names()
_FIELD_NAME_RE = {
    n: re.compile(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    for n in KNOWN_FIELD_NAMES
}

# 第 77 条：哪些「待核实」注释**永远不许**建议删 —— 即使它点名的字段都已填齐。
#
# 任务书第三节「不确定的值不要猜」（`SECTION3_CLAUSES["no_guessing"]`，第 7 条）要求
# 「宁可保留原值，并用注释写出两种可能」。
# 这类注释记的是一个**悬着的问题**（值有两说 / 疑似笔误 / 版本没定），字段填着也不能删：
# 删掉就等于把「这里还没核完」这条审计线索抹掉了。只有第 6 条那种「查不到就省略字段，
# 并写 `% ⚠ 待核实：<字段>`」的注释，字段补上以后才算**过期**注释。
#
# （反面：`暂缺` / `未直接给出` 说的是「字段当初就缺」，不在此列——那类补上后才该删。）
#
# ⚠️ 这一档是「第 7 条 vs 第 6 条」的分界：把它设成空元组 `()`，判据就退化成
# 「只看注释点名的字段是否还缺」（更宽松：`Maddy2002`/`Martin2025`/`LiLei2023`
# 那种「值两说」的注释也会被建议删）。留这一行是为了让口径可切换、可测。
VALUE_STILL_IN_DOUBT: tuple[str, ...] = (
    "两说", "两可", "两版", "存疑", "疑为", "疑似",
    "未核实", "未确认", "未能核实", "无法确认", "无法核实", "无法访问",
    "暂按", "暂依", "保留", "待考", "建议确认", "建议核",
)


def pointed_fields(comments: Iterable[str]) -> list[str]:
    """注释里**点名**的字段（第 77 条：用 `bibcheck.REQUIRED` 的字段表认，不另列一份）。

    只看 `待核实` 标记**之后、第一个括号之前**的那一段（即
    `% ⚠ 待核实：<字段…>（原因）` 的 `<字段…>`）。为什么不扫全句：原因部分会出现
    普通词/缩写（`URL`、`edition`、`note`…），它们与字段表里的 `url` / `note` 撞名，
    会凭空点出一个根本没提的字段。没有 `待核实` 标记的注释（`% 来源：` / `% 类型已改：`）
    不参与。

    返回按字段名排序、去重的列表（顺序确定，测试与报告都不抖）。
    `booktitle` 不会被误认成 `title` —— 正则两侧都禁掉了字母/数字。
    """
    names: set[str] = set()
    for c in comments:
        i = c.find("待核实")
        if i < 0:
            continue
        head = c[i + len("待核实"):]
        for ch in "（(":
            j = head.find(ch)
            if j >= 0:
                head = head[:j]
        for n in KNOWN_FIELD_NAMES:
            if _FIELD_NAME_RE[n].search(head):
                names.add(n)
    return sorted(names)


def value_still_in_doubt(comments: Iterable[str]) -> bool:
    """注释是否还在记一个**悬着的问题**（第 77 条：这类注释永远不建议删）。"""
    text = "\n".join(comments)
    return any(marker in text for marker in VALUE_STILL_IN_DOUBT)


def check_spec(rep: Report, entries: list[Entry]) -> None:
    """规范核对：必填字段复用 `bibcheck.check`（**逐条**跑，损坏条目不会污染邻条），
    值层面的写法复用 `bibnorm` 的规则函数与常量。"""
    order_bad: list[str] = []  # 字段顺序只汇总一条，免得几百条提示淹掉真问题
    for e in entries:
        if e.broken:
            rep.add(
                e.key,
                "spec",
                LEVEL_INFO,
                "这条结构已损坏，字段核对跳过（先修「一、可解析性」里报出的语法问题）",
            )
            continue

        # ── 必填字段：完全交给既有规则 ──
        #
        # 任务书第三节「查不到的字段就省略该字段 + 待核实注释」
        # （`SECTION3_CLAUSES["missing_field_note"]`，第 6 条）允许「查不到的字段就省略该字段，
        # 并在条目上方加 `% ⚠ 待核实：<字段>`」。所以只在这种**明示**下把缺字段降为提示
        # （仍要编辑补），悄悄少一个字段才是问题——那正是默认档。
        one = f"@{e.etype}{{{e.key},{e.body}}}"
        declared = [c for c in e.comments if "待核实" in c]
        missing_must: list[str] = []
        for iss in bibcheck.check(one).issues:
            if iss.severity == "must":
                if iss.kind in ("missing", "empty"):
                    missing_must.append(iss.detail)
                    named = any(iss.detail in c for c in declared)
                    what = "缺失" if iss.kind == "missing" else "是空的"
                    if named:
                        rep.add(
                            e.key,
                            "spec",
                            LEVEL_INFO,
                            f"必填字段{what}：`{iss.detail}`——条目上方已按任务书第三节第 "
                            f"{clause_no('missing_field_note')} 条注明"
                            "「待核实」，查不到允许省略，但编辑仍需补",
                        )
                    elif declared:
                        rep.add(
                            e.key,
                            "spec",
                            LEVEL_PROBLEM,
                            f"必填字段{what}：`{iss.detail}`（该条上方有「待核实」注释，"
                            f"但注释里没有点名 `{iss.detail}`，看不出是查不到还是漏写）",
                        )
                    else:
                        rep.add(
                            e.key,
                            "spec",
                            LEVEL_PROBLEM,
                            f"必填字段{what}：`{iss.detail}`（@{iss.etype} 的必备字段见任务书第一节；"
                            f"查不到就照第三节第 {clause_no('missing_field_note')} 条在该条上方写 "
                            "`% ⚠ 待核实：…`）",
                        )
                else:
                    rep.add(e.key, "spec", LEVEL_PROBLEM, iss.detail)
            elif iss.severity == "unstructured" or iss.kind == "unstructured":
                rep.add(e.key, "spec", LEVEL_INFO, f"整条在 note 里、未拆成字段：{iss.detail}")
            elif iss.kind == "type":
                rep.add(e.key, "spec", LEVEL_INFO, f"类型/写法提示：{iss.detail}")
            else:
                rep.add(e.key, "spec", LEVEL_INFO, f"建议补字段：`{iss.detail}`")

        # ── 「待核实」注释：什么时候才真的是**过期**注释？（第 77 条）──
        #
        # 旧判据只看 must 字段是否缺（`declared and not missing_must`），撞两条规则：
        #   ① 第 7 条要求保留「不确定的值不要猜」的注释（值有两说 / 疑似笔误 / 版本未定）
        #      ——这种注释字段填着也必须留（真实稿：Maddy2002 的 journal 两说、
        #      Martin2025 的 publisher 疑为 `Press` 之误、LiLei2023「暂按源稿原文保留」）；
        #   ② 注释点名的字段可能只是 **nice**（如 @book 的 edition）——它不在 must 表里，
        #      `missing_must` 于是为空，被误判成「都已填齐」，可**点名的那个字段其实还缺**
        #      （真实稿：Quine1970 / Williamson2007 的 `% ⚠ 待核实：edition`）。
        #
        # 现判据（只报不改，一个 bib 字节都不动）：
        #   · 注释点名的任何字段（must 或 nice）**只要还缺一个** → 绝不建议删；
        #   · 注释还在记悬着的问题（`value_still_in_doubt`）→ 绝不建议删；
        #   · 注释没点名任何字段、或点名的字段都已填齐，且 must 字段齐、且没有悬着的问题
        #     → 才是真过期注释（例如 LiLei2022：注释写「卷号暂缺」但 `volume = {15}`
        #     已经补上；或注释写「已按官方来源核实，可删」）。
        named = pointed_fields(declared) if declared else []
        named_missing = [n for n in named if not (e.fields.get(n, "") or "").strip()]
        if declared and not named_missing and not missing_must and not value_still_in_doubt(declared):
            rep.add(
                e.key,
                "spec",
                LEVEL_INFO,
                "条目上方有「待核实」注释，但必备字段都已填齐，建议删掉该注释",
            )

        f = e.fields

        # ── doi / isbn 一律不写（编辑部 2026-10-14）──
        for name in ("doi", "isbn"):
            if f.get(name, "").strip():
                rep.add(
                    e.key,
                    "spec",
                    LEVEL_PROBLEM,
                    f"`{name}` 不该写进 bib（只用于核实）：{name} = {{{_clip(f[name], 40)}}}",
                )

        # ── 页码：双短横、不写 pp.（复用 bibnorm 的清洗规则做对照）──
        if f.get("pages", "").strip():
            raw = f["pages"].strip()
            cleaned = bibnorm._clean_field_value("pages", raw)
            if cleaned is not None and cleaned != raw:
                rep.add(
                    e.key,
                    "spec",
                    LEVEL_PROBLEM,
                    f"页码写法不对（应用双短横、不写 pp.）：pages = {{{_clip(raw, 30)}}}"
                    f" → 应为 {{{_clip(cleaned, 30)}}}",
                )

        # ── 期号前导零（复用 bibnorm 的清洗规则）──
        for name, level in (("number", LEVEL_PROBLEM), ("volume", LEVEL_INFO)):
            if f.get(name, "").strip():
                raw = f[name].strip()
                cleaned = bibnorm._clean_field_value(name, raw)
                if cleaned is not None and cleaned != raw:
                    rep.add(
                        e.key,
                        "spec",
                        level,
                        f"{'期号' if name == 'number' else '卷号'}有前导零：{name} = {{{_clip(raw, 20)}}}"
                        f" → 应为 {{{_clip(cleaned, 20)}}}",
                    )

        # ── 月份：三字母缩写（复用 bibnorm._MONTHS）──
        if f.get("month", "").strip():
            raw = f["month"].strip()
            cleaned = bibnorm._clean_field_value("month", raw)
            if (cleaned or "").strip().lower() not in bibnorm._MONTHS:
                rep.add(
                    e.key,
                    "spec",
                    LEVEL_PROBLEM,
                    f"月份应写三字母缩写（如 nov）：month = {{{_clip(raw, 20)}}}",
                )

        # ── userf：中文条目必须 `{cn}`（复用 bibnorm.detect_userf）──
        au = f.get("author", "") or f.get("editor", "")
        ti = f.get("title", "")
        expect = bibnorm.detect_userf(au, ti)
        got = f.get("userf", "").strip()
        if expect and got != expect:
            rep.add(
                e.key,
                "spec",
                LEVEL_PROBLEM,
                f"中文/日文条目必须带 userf = {{{expect}}}（`SCLN-MIX.sty` 据此改用中文标点），"
                f"现状：{'未填' if not got else f'userf = {{{got}}}'}",
            )
        elif not expect and got:
            rep.add(
                e.key,
                "spec",
                LEVEL_INFO,
                f"纯英文条目按规范不填 userf（现状 userf = {{{got}}}）",
            )

        # ── 英文作者 / 编者一律倒装（编辑部 2026-10-15 **硬规则**）──
        #
        # 判据与规范化器同源（`slcn/bibcheck.author_form_issues` → `bibnorm`），
        # 所以报出来的每一条都能给出确切的改法。中/日文姓名与判不准的英文名不报。
        for iss in bibcheck.author_form_issues(one):
            rep.add(e.key, "spec", LEVEL_PROBLEM, iss.detail)

        # ── 中文译著缺「（著）/（译）」标记 → **提示**（不挡退出码）──
        #
        # 噪声实测见 docs/cases/62+.md 第 63 条：全库 62 个 bib 里只有 3 个文件命中。
        for iss in bibcheck.translation_marking_issues(one):
            rep.add(e.key, "spec", LEVEL_INFO, iss.detail)

        # ── 本刊固定写法：保护花括号 / 英文书版次 / 只有编者的书 → **提示**（第 85 条）──
        #
        # 为什么是提示不是问题：这些是「值该怎么写」，不影响这条文献能不能用；
        # 而且判据是**排版机制**推出来的（sty 对英文 article/incollection… 的 title
        # 走 \MakeSentenceCase，没保护的大写会被压成小写），编辑照提示改即可。
        for iss in bibcheck.style_form_issues(one):
            rep.add(e.key, "spec", LEVEL_INFO, iss.detail)

        # ── 多作者：写法可规范化（复用 bibnorm 的清洗规则做对照；中文顿号/逗号 → and）──
        #
        # ⚠️ 中文译著 / 整理者体例（`原作者（著），译者（译）`，含（点校）（编）等）**豁免**：
        #    那个中文逗号与顿号是**姓名的一部分**，不是多作者分隔符；按普通中文多作者的
        #    口径改成 `and` 会把 `{阿斯海姆（著），郑建国、万林（译）}` 变成
        #    `{阿斯海姆（著） and 郑建国 and 万林（译）}`，**著/译标记就此毁掉**（第 64 条，
        #    2026-10-15 在真实成品上发现）。这类值改由 `translation_marking_issues()`
        #    按中文译著体例校验（缺（著）/缺（译）/误用 `and`），**不在这里报**。
        raw_au = f.get("author", "").strip()
        if raw_au:
            if bibcheck.author_has_role_mark(raw_au):
                pass
            else:
                clean_au = bibnorm._clean_field_value("author", raw_au)
                if clean_au and clean_au != raw_au:
                    rep.add(
                        e.key,
                        "spec",
                        LEVEL_INFO,
                        f"作者写法可规范（多作者用 `and` 分隔）：{{{_clip(raw_au, 50)}}} → "
                        f"{{{_clip(clean_au, 50)}}}",
                    )

        # ── 多作者：逗号与 and 混用（任务书第一节第 2 条的实测错名案例）──
        if raw_au:
            seg = raw_au
            if " and " in seg and "," in seg:
                for part in seg.split(" and "):
                    head = part.split(",")[0].strip()
                    if "," in part and len(head.split()) > 1:
                        rep.add(
                            e.key,
                            "spec",
                            LEVEL_INFO,
                            "作者里逗号与 and 混用：BibTeX 把逗号当「姓, 名」倒装符，"
                            f"会排出错名（…{_clip(head, 30)},…），请统一改成 and 或统一用倒装式",
                        )
                        break

        # ── 编辑惯例不收的字段（复用 bibnorm.DROP_FIELDS）──
        for name in sorted(set(f) & bibnorm.DROP_FIELDS):
            if f.get(name, "").strip():
                rep.add(e.key, "spec", LEVEL_INFO, f"字段 `{name}` 编辑惯例不收，建议删掉")

        # ── 字段顺序（任务书第三节：顺序固定）──
        present = [n for n in f if n in SPEC_FIELD_ORDER]
        idx = [SPEC_FIELD_ORDER.index(n) for n in present]
        if idx != sorted(idx):
            order_bad.append(e.key)

    if order_bad:
        shown = "、".join(f"`{k}`" for k in order_bad[:5])
        more = f" 等 {len(order_bad)} 条" if len(order_bad) > 5 else ""
        rep.add(
            "",
            "spec",
            LEVEL_INFO,
            f"字段顺序与任务书第三节的固定顺序不一致：{shown}{more}"
            "（规范：author → title → journal/booktitle/editor → publisher → address"
            " → edition → volume → number → pages → year → userf）",
        )


# ── ③ 引用键闭合 ──────────────────────────────────────────────────────────


def check_citations(rep: Report, tex: str, entries: list[Entry]) -> None:
    r"""正文 `\parencite{…}` / `\cite{…}` ↔ bib 键的双向核对。

    引用命令的抽取完全用 `bibcheck.cited_keys`（它认 `\xxxcite*[]{…}` 的各种写法），
    这里不重写正则。
    """
    rep.cited = bibcheck.cited_keys(tex)
    rep.n_cite_cmds = len(bibcheck._CITE.findall(tex))  # 仅供报告说明用
    bibkeys = {e.key for e in entries if e.key}

    if not rep.cited:
        rep.add(
            "",
            "cite",
            LEVEL_INFO,
            "正文里没有找到任何 `\\cite` / `\\parencite` 命令，引用闭合无从核对",
        )
        if bibkeys:
            # P2-4（2026-10-18）：bib 有条目但正文 0 引用命令 → 参考文献区会是空的，
            # 这是**内容缺失**（期 87 sub5/sub7 实测），从 hint 升 problem。
            rep.add(
                "",
                "cite",
                LEVEL_PROBLEM,
                f"正文里没有找到任何引用命令，但参考文献里有 {len(bibkeys)} 条 —— "
                f"**PDF 参考文献区会是空的**：请补正文引用（或删掉未引用的条目）",
            )
    for k in sorted(rep.cited - bibkeys):
        rep.add(
            k,
            "cite",
            LEVEL_PROBLEM,
            f"悬空引用：正文引用了 `\\parencite{{{k}}}`（或同类命令），但退回的 .bib 里没有这个键"
            "——编译会报 `Citation '…' undefined`，正文出现 `?`",
        )
    for k in sorted(bibkeys - rep.cited):
        rep.add(
            k,
            "cite",
            LEVEL_INFO,
            f"`{{{k}}}` 在 bib 里，但正文从未引用（多余条目，或正文里的 key 被改错了）",
        )


# ── ④ 与 --original 的 diff ───────────────────────────────────────────────


def _norm_val(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _title_sig(e: Entry) -> str:
    t = _norm_val(e.fields.get("title", "")).lower()
    t = re.sub(r"[{}~\\]", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    return t


def _author_year_sig(e: Entry) -> str:
    au = _norm_val(e.fields.get("author", "") or e.fields.get("editor", "")).lower()
    au = re.sub(r"[^\w\u4e00-\u9fff]+", "", au)
    yr = re.sub(r"\D", "", e.fields.get("year", "") or e.fields.get("date", ""))
    return f"{au}|{yr}" if au and yr else ""


def check_diff(rep: Report, orig_entries: list[Entry], entries: list[Entry]) -> None:
    """逐键比字段。问题级：key 改名 / 条目被删 / 字段被删 / 类型被改。"""
    okeys = [e.key for e in orig_entries]
    omap: dict[str, Entry] = {}
    for e in orig_entries:
        omap.setdefault(e.key, e)
    nmap: dict[str, Entry] = {}
    for e in entries:
        nmap.setdefault(e.key, e)
    rows: dict[str, DiffRow] = {}
    nkeys = [e.key for e in entries if e.key]
    oks, nks = set(okeys), set(nkeys)
    overlap = (len(oks & nks) / max(len(oks), 1)) if oks else 1.0
    # P1-1/P2-5（2026-10-18）：key 方案不通用时（Word 通道按键重新造键，与人工键
    # 100% 不同）**自动降级 title 匹配**：按 title 配上的对子只做字段比对、不再报
    # 「疑似改了 key」，并把「按 key / 按 title」两套统计写进报告。
    degraded = (bool(nkeys) and overlap < 0.5
                and max(len(oks), len(nkeys)) >= 8)   # 小清单仍按旧口径（改 key=问题）
    title_pairs: list[tuple[str, str]] = []
    silenced = {"drop": 0, "howpublished": 0}

    def compare_fields(o: Entry, n: Entry, row: DiffRow) -> None:
        """逐字段比对（类型 + 字段增删改），把发现写进 rep 并更新 row。"""
        k = row.key
        ot_raw, nt_raw = o.etype, n.etype
        ot, nt = ot_raw.lower(), nt_raw.lower()
        if ot != nt:
            row.kind = "changed"
            if bibcheck.TYPE_ALIASES.get(ot, ot) == bibcheck.TYPE_ALIASES.get(nt, nt):
                rep.add(
                    k, "diff", LEVEL_INFO,
                    f"类型按规范统一：@{ot_raw} → @{nt_raw}（任务书第一节要求）",
                )
            elif any("类型已改" in c for c in n.comments):
                rep.add(
                    k, "diff", LEVEL_INFO,
                    f"条目类型改变：@{ot_raw} → @{nt_raw}（条目上方有 `% 类型已改` 注释，"
                    f"符合任务书第三节第 {clause_no('type_changed')} 条）",
                )
            else:
                rep.add(
                    k, "diff", LEVEL_PROBLEM,
                    f"条目类型被改：@{ot_raw} → @{nt_raw}（任务书第三节第 {clause_no('type_changed')} 条："
                    f"改了类型必须在条目上方"
                    f"加 `% 类型已改：@{ot_raw} → @{nt_raw}（理由）`）。类型判错会让排版样式整体错位",
                )

        of, nf = o.fields, n.fields
        from . import bibnorm as _bibnorm     # 本刊规范口径（DROP_FIELDS / SEP 判据）
        for name in of:
            if name not in nf:
                # P1-5（2026-10-18）：本刊规范本来就不收的字段 + SEP 的 howpublished→url，
                # 白名单静音（旧行为会把 49 urldate + 47 howpublished 报成「字段被删」假差异）。
                if name in _bibnorm.DROP_FIELDS:
                    silenced["drop"] += 1
                    row.notes.append(f"去掉 {name}（本刊规范）")
                    rep.add(
                        k, "diff", LEVEL_INFO,
                        f"按本刊规范不写 `{name}`（DROP_FIELDS，原件有 = "
                        f"{{{_clip(of[name], 30)}}}）：静音，不计假差异",
                    )
                    continue
                if (name == "howpublished" and nf.get("url", "").strip()
                        and _bibnorm.is_sep_entry(of)):
                    silenced["howpublished"] += 1
                    row.notes.append("howpublished→url（规范）")
                    rep.add(
                        k, "diff", LEVEL_INFO,
                        f"按规范 `howpublished` → `url`（SEP 词条，第 84 条）：原件 = "
                        f"{{{_clip(of[name], 30)}}}，退回版已写 url ——静音，不计假差异",
                    )
                    continue
                if name in ("doi", "isbn"):
                    rep.add(
                        k, "diff", LEVEL_INFO,
                        f"已按规范去掉 `{name}` = {{{_clip(of[name], 30)}}}（正确：doi/isbn 只用于核实）",
                    )
                    row.notes.append(f"去掉 {name}（正确）")
                    continue
                row.kind = "changed"
                row.removed.append(name)
                rep.add(
                    k, "diff", LEVEL_PROBLEM,
                    f"字段被删掉：`{name}`（原件有 = {{{_clip(of[name], 40)}}}，退回版里没有）"
                    f"——任务书第三节第 {clause_no('all_known_fields')} 条要求「每条写全所有已知字段」",
                )
            elif of[name].strip() and not nf[name].strip():
                row.kind = "changed"
                row.removed.append(name)
                rep.add(
                    k, "diff", LEVEL_PROBLEM,
                    f"字段被清空：`{name}`（原件 = {{{_clip(of[name], 40)}}}，退回版是空值）",
                )
        for name in nf:
            if name not in of and nf[name].strip():
                row.kind = "changed"
                row.added.append(name)
                rep.add(
                    k, "diff", LEVEL_INFO,
                    f"新增字段：`{name}` = {{{_clip(nf[name], 40)}}}"
                    f"（任务书第三节第 {clause_no('no_guessing')} 条：不确定的值不要猜）",
                )
        for name in of:
            if name in nf and of[name].strip() and nf[name].strip():
                if _norm_val(of[name]) != _norm_val(nf[name]):
                    row.kind = "changed"
                    row.changed.append(name)
                    rep.add(
                        k, "diff", LEVEL_INFO,
                        f"字段值有改动：`{name}`：`{_clip(of[name], 40)}` → `{_clip(nf[name], 40)}`",
                    )

    # ── 逐键比对（两边都有的键）──
    for k in okeys:
        if k not in nmap:
            continue
        o, n = omap[k], nmap[k]
        row = DiffRow(key=k, kind="same", otype=o.etype, ntype=n.etype)
        rows[k] = row
        compare_fields(o, n, row)

    # ── 只在原件里的键：先当「改名」找，找不到就是「删除」──
    only_old = [k for k in okeys if k not in nmap]
    only_new = [k for k in [e.key for e in entries] if k not in omap and k]
    renamed: dict[str, str] = {}
    used: set[str] = set()
    for ok in only_old:
        o = omap[ok]
        for matcher, why in ((_title_sig, "title 一致"), (_author_year_sig, "author+year 一致")):
            sig = matcher(o)
            if not sig:
                continue
            cand = [nk for nk in only_new if nk not in used and sig == matcher(nmap[nk])]
            if cand:
                nk = cand[0]
                renamed[ok] = nk
                used.add(nk)
                if degraded:
                    # 降级模式：键不同是**生成方案差异**，不是「改 key」错误 → 只算提示
                    title_pairs.append((ok, nk))
                    rep.add(
                        ok, "diff", LEVEL_INFO,
                        f"按 {why} 匹配：`{ok}` ↔ `{nk}`（两套键方案不同；key 重叠率低 → "
                        f"自动降级 title 匹配，不计「改 key」问题）",
                    )
                else:
                    rep.add(
                        ok, "diff", LEVEL_PROBLEM,
                        f"疑似改了 key：`{ok}` → `{nk}`（{why}）。任务书第三节第 "
                        f"{clause_no('key_unchanged')} 条：**key 一律不改**"
                        f"——正文里的 `\\parencite{{{ok}}}` 会全部悬空",
                    )
                row = DiffRow(
                    key=nk, kind="renamed", old_key=ok, otype=o.etype, ntype=nmap[nk].etype
                )
                rows[nk] = row
                compare_fields(o, nmap[nk], row)
                if row.kind == "changed":  # 改名本身是最响亮的问题，状态仍标 renamed
                    row.kind = "renamed"
                break

    for ok in only_old:
        if ok in renamed:
            continue
        rows[ok] = DiffRow(key=ok, kind="deleted", otype=omap[ok].etype)
        rep.add(
            ok, "diff", LEVEL_PROBLEM,
            f"条目被删除：原件有 `{ok}`（@{omap[ok].etype}），退回版里既没有这个键，"
            "也没有 title/author 相同的替代条目",
        )

    for nk in only_new:
        if nk in used:
            continue
        rows[nk] = DiffRow(key=nk, kind="added", ntype=nmap[nk].etype)
        rep.add(nk, "diff", LEVEL_INFO, f"新增条目：`{nk}`（原件里没有；请确认是补的文献还是误加）")

    rep.diff = [rows[k] for k in rows]
    rep.match_stats = {
        "key": len(oks & nks),
        "title": len(title_pairs),
        "degraded": degraded,
        "overlap": round(overlap, 4),
        "silenced": silenced,
    }
    if degraded:
        rep.add(
            "", "diff", LEVEL_INFO,
            f"diff 匹配口径：key 重叠率 {overlap:.0%} < 50% → **自动降级 title 匹配**；"
            f"按 key 匹配 {len(oks & nks)} 条 / 按 title 匹配 {len(title_pairs)} 条。"
            "键不同不再报「改 key」问题（生成方案差异，不代表编辑改键）。",
        )
    if silenced["drop"] or silenced["howpublished"]:
        rep.add(
            "", "diff", LEVEL_INFO,
            f"diff 静音口径（P1-5）：按本刊规范不写 DROP_FIELDS 字段 "
            f"{silenced['drop']} 处、SEP `howpublished`→`url` "
            f"{silenced['howpublished']} 处，均不计假差异。",
        )


# ── 组装 + 渲染 ───────────────────────────────────────────────────────────


def read_text(path: str) -> tuple[str, str]:
    """读文本：按 UTF-8 / GB18030 依次尝试。返回 (文本, 提示)。编码认不出时降级读，不报错。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        note = "" if enc == "utf-8" else f"`{path}` 不是 UTF-8，已按 {enc} 读入（请确认没有乱码）"
        return text, note
    return raw.decode("utf-8", errors="replace"), f"`{path}` 的编码认不出来，已用替换字符读入（部分字段可能看不准）"


def analyze(
    returned_text: str,
    original_text: str | None = None,
    tex_text: str | None = None,
    *,
    returned_path: str = "",
    original_path: str = "",
    tex_path: str = "",
) -> Report:
    """跑完四类核对，返回 Report。"""
    rep = Report(
        returned=returned_path or "(stdin/字符串)",
        original=original_path,
        tex=tex_path,
    )
    entries, syntax_findings = parse_entries(returned_text)
    rep.entries = entries
    rep.total = len(entries)
    rep.findings.extend(syntax_findings)

    check_spec(rep, entries)
    if tex_text is not None:
        check_citations(rep, tex_text, entries)
    if original_text is not None:
        orig_entries, orig_findings = parse_entries(original_text)
        check_diff(rep, orig_entries, entries)
        n_orig_broken = len([f for f in orig_findings if f.level == LEVEL_PROBLEM])
        if n_orig_broken:
            rep.add(
                "",
                "diff",
                LEVEL_INFO,
                f"原件本身有 {n_orig_broken} 处语法问题；diff 是在「尽力解析」的原件上做的，"
                "只有退回版的问题才算数",
            )
    return rep


def _group_findings(rep: Report, section: str) -> tuple[list[Finding], list[Finding]]:
    seen: set[tuple[str, str, str]] = set()
    probs: list[Finding] = []
    infos: list[Finding] = []
    for f in rep.findings:
        if f.section != section:
            continue
        sig = (f.key, f.level, f.detail)
        if sig in seen:
            continue
        seen.add(sig)
        (probs if f.level == LEVEL_PROBLEM else infos).append(f)
    key = lambda f: (f.key != "", f.key, f.detail)  # noqa: E731  文件级发现排最前
    return sorted(probs, key=key), sorted(infos, key=key)


def _bullets(items: list[Finding], limit: int = BULLET_LIMIT) -> list[str]:
    out = []
    for f in items[:limit]:
        who = f"`{f.key}`" if f.key else "（整份文件）"
        out.append(f"- {who}：{f.detail}")
    if len(items) > limit:
        out.append(f"- …另有 {len(items) - limit} 条同类发现（逐条状态见附录）")
    return out


def _diff_status(row: DiffRow | None) -> str:
    if row is None:
        return "—"
    if row.kind == "same":
        return "未变（逐字段一致）" if not row.notes else "未变；" + "、".join(row.notes)
    if row.kind == "renamed":
        extra = []
        if row.removed:
            extra.append(f"少 {len(row.removed)} 个字段")
        if row.added:
            extra.append(f"多 {len(row.added)} 个字段")
        if row.changed:
            extra.append(f"改 {len(row.changed)} 个字段值")
        tail = f"；{'、'.join(extra)}" if extra else ""
        return f"疑似改名 ← `{row.old_key}`{tail}"
    if row.kind == "deleted":
        return "原件有、退回版没有"
    if row.kind == "added":
        return "新增"
    bits = []
    if row.otype and row.ntype and row.otype.lower() != row.ntype.lower():
        bits.append(f"类型 @{row.otype}→@{row.ntype}")
    if row.removed:
        bits.append(f"少 {len(row.removed)} 个字段")
    if row.added:
        bits.append(f"多 {len(row.added)} 个字段")
    if row.changed:
        bits.append(f"改 {len(row.changed)} 个字段值")
    bits.extend(row.notes)
    return "、".join(bits) or "有改动"


def render(
    rep: Report,
    title: str = "退回版参考文献核对报告",
    source_note: str = SOURCE_NOTE_CHECKBIB,
) -> str:
    """渲染 markdown 报告：结论 → 分节 → 附录逐条状态。"""
    L: list[str] = [f"# {title}", ""]
    verdict = "✅ 未发现问题" if rep.ok else f"❗ 有 {rep.n_problem} 处问题需要处理"
    L.append(f"**结论：{rep.n_problem} 处问题 / {rep.n_info} 处提示** —— {verdict}")
    L.append("")
    entries_txt = f"{rep.total} 条条目"
    L.append(f"- 退回文件：`{rep.returned or '—'}`（解析出 {entries_txt}）")
    L.append(
        f"- 原件（--original）：`{rep.original}`" if rep.original else "- 原件（--original）：**未提供**（不做字段级 diff）"
    )
    L.append(
        f"- 正文（--tex）：`{rep.tex}`" if rep.tex else "- 正文（--tex）：**未提供**（不做引用键闭合核对）"
    )
    stats = rep.match_stats or {}
    if stats.get("degraded"):
        L.append(
            f"- diff 匹配：**按 key {stats.get('key', 0)} 条 / 按 title "
            f"{stats.get('title', 0)} 条**（key 重叠率 {stats.get('overlap', 0):.0%} < 50% → "
            "自动降级 title 匹配；键不同不计「改 key」问题）"
        )
    if stats.get("silenced"):
        sil = stats["silenced"]
        L.append(
            f"- diff 静音：DROP_FIELDS {sil.get('drop', 0)} 处 / SEP "
            f"howpublished→url {sil.get('howpublished', 0)} 处（本刊规范，不计假差异）"
        )
    for nt in rep.notes:
        L.append(f"- 说明：{nt}")
    L.append("")

    for sec in SECTION_ORDER:
        if sec == "cite" and not rep.tex:
            continue
        if sec == "diff" and not rep.original:
            continue
        probs, infos = _group_findings(rep, sec)
        L.append(f"## {SECTION_TITLES[sec]}")
        L.append("")
        if not probs and not infos:
            L.append("✅ 本节未发现问题。")
            L.append("")
            continue
        if probs:
            L.append(f"**问题（{len(probs)}）**")
            L.append("")
            L += _bullets(probs)
            L.append("")
        if infos:
            L.append(f"**提示（{len(infos)}）**")
            L.append("")
            L += _bullets(infos)
            L.append("")

        # diff 节额外给一行「未改动」汇总（不逐条刷屏）
        if sec == "diff":
            # 「未改动」= 逐字段一致、且没有像「按规范去掉 doi」这种备注
            same = [r.key for r in rep.diff if r.kind == "same" and not r.notes]
            if same:
                shown = "、".join(f"`{k}`" for k in same[:60])
                more = f"（另 {len(same) - 60} 条）" if len(same) > 60 else ""
                L.append(f"未改动的条目 {len(same)} 条：{shown}{more}")
                L.append("")

    # ── 附录：逐条状态 ──
    per_key_p: dict[str, int] = {}
    per_key_i: dict[str, int] = {}
    for f in rep.findings:
        if f.level == LEVEL_PROBLEM:
            per_key_p[f.key] = per_key_p.get(f.key, 0) + 1
        else:
            per_key_i[f.key] = per_key_i.get(f.key, 0) + 1
    drow: dict[str, DiffRow] = {r.key: r for r in rep.diff}

    L.append("## 附录：逐条状态")
    L.append("")
    L.append("| 键 | 类型 | 问题 | 提示 | 与原件 |")
    L.append("|---|---|---|---|---|")
    if not rep.entries:
        L.append("| —（没有解析到任何条目） | — | — | — | — |")
    for e in rep.entries:
        etype = f"@{e.etype}" + ("（结构损坏）" if e.broken else "")
        L.append(
            f"| `{e.key or '（无键）'}` | {etype} | {per_key_p.get(e.key, 0)} "
            f"| {per_key_i.get(e.key, 0)} | {_diff_status(drow.get(e.key)) if rep.original else '未比对'} |"
        )
    # 只出现在原件里的键（被删/被改名）
    for r in rep.diff:
        if r.kind in ("deleted",):
            L.append(
                f"| `{r.key}` | @{r.otype} | {per_key_p.get(r.key, 0)} | {per_key_i.get(r.key, 0)} "
                f"| {_diff_status(r)} |"
            )
    L.append("")
    L.append(source_note)
    L.append(">")
    L.append(
        "> ⚠️ 本报告只回答「**退回的 bib 与任务书规范、原件、正文是否自洽**」；"
        "卷/期/页码/出版地是否与官方来源一致，仍由编辑按任务书第二节核实——工具不联网、不判断真伪。"
    )
    # ── B-285a 摘要头：结论复用 `conclusion_line()` 单源，正文一个字不改 ──
    problems = [f for f in rep.findings if f.level == LEVEL_PROBLEM]
    infos = [f for f in rep.findings if f.level != LEVEL_PROBLEM]

    def _finding_line(f: Finding) -> str:
        where = SECTION_TITLES.get(f.section, f.section)
        return f"[{where}] `{f.key or '（整份文件）'}`：{f.detail}"

    header = _rh.render(
        conclusion=conclusion_line(rep),
        counts=(("问题", rep.n_problem), ("提示", rep.n_info), ("条目", rep.total)),
        top=[_finding_line(f) for f in problems] + [_finding_line(f) for f in infos],
        details=[_finding_line(f) for f in rep.findings],
    )
    return _rh.insert("\n".join(L), header)


def conclusion_line(rep: Report) -> str:
    """报告第一行结论（控制台日志用；与 markdown 里那行**同源**，不另写一份口径）。"""
    verdict = "✅ 未发现问题" if rep.ok else f"❗ 有 {rep.n_problem} 处问题需要处理"
    return f"结论：{rep.n_problem} 处问题 / {rep.n_info} 处提示 —— {verdict}"


def build_report_and_conclusion(
    returned_text: str,
    original_text: str | None = None,
    tex_text: str | None = None,
    title: str = "退回版参考文献核对报告",
    *,
    returned_path: str = "",
    original_path: str = "",
    tex_path: str = "",
    notes: tuple[str, ...] | list[str] = (),
    source_note: str = SOURCE_NOTE_CHECKBIB,
) -> tuple[str, str]:
    """**唯一**的报告入口：一份 bib 文本 → (四节 markdown, 结论行)。

    `checkbib.py` 走「读文件 → 本函数 → 打印/落盘」，`convert.py` 走
    「内存里的合并结果 → 本函数 → `<stem>_回读校验报告.md` + 控制台结论行」。
    两个返回值**同源**（同一次 `analyze()`）：结论行不是从 markdown 里正则扒的，
    否则又成了第二套口径。
    """
    rep = analyze(
        returned_text,
        original_text,
        tex_text,
        returned_path=returned_path,
        original_path=original_path,
        tex_path=tex_path,
    )
    rep.notes = list(notes) + rep.notes
    return render(rep, title, source_note=source_note), conclusion_line(rep)


def build_report(
    returned_text: str,
    original_text: str | None = None,
    tex_text: str | None = None,
    title: str = "退回版参考文献核对报告",
    **kw,
) -> str:
    """只要 markdown 时的便捷入口（`build_report_and_conclusion()` 的前半）。"""
    return build_report_and_conclusion(returned_text, original_text, tex_text, title, **kw)[0]
