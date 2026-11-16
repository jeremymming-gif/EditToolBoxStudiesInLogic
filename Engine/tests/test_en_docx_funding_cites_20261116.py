#!/usr/bin/env python3.14
r"""英文 Word 稿：基金项目落槽（P1）+ 正文编号引用转 `\parencite`（P2）—— 2026-11-16。

两处缺陷（同一张任务卡，两半）
------------------------------
P1 英文模板只有 `\mythanks` 一槽（`\newcommand{\mythanks}{null}`），没有
`\mygrants`。旧链路把基金写进**不存在的** `\mygrants` → `set_field` 静默失败，
基金号既没进 `\mythanks` 也没报错，PDF 标题脚注是空的。

P2 正文里的 `([18, p. 398])`（页码写在方括号**里面**）不被 `_NUM_GROUP` 匹配
（它只认纯编号 `[18]`/`[18,19]`），于是整组原样留在 .tex 里 → PDF 里是硬写的
「[18, p. 398]」，文献区没有对应条目。

修法
----
P1 `slcn/latex.py` 新增 `editorial_slots()`/`split_slot_text()`/`join_slot_text()`/
`live_field_value()`；`slcn/build.py` 新增 `apply_editorial_fields()`（按模板**真实**
槽位落值：两槽分离则各写各的，单槽则「致谢；基金」拼接去重）与
`editorial_fields()`；`slcn/metadata_source.py` 的 `_CANON_MACRO["en"]` 把
grants/thanks 都指向 `mythanks` 并留 `mygrants→grants` 兼容别名；
`slcn/texfront.py: template_values(tpl_fields)` 同样按槽落值。

P2 `slcn/wordcitebody.py` 新增 `_NUM_GROUP_LOC`（只收**带定位标记**的页码段，
不收裸数字，故 `[18,19]` 绝不会被拆成「编号 18 + 页码 19」）与
`_link_bare_paged_citations`（括号组之外的 `[2, p.187]` 通道）。

本套件钉住什么
--------------
P1 七条：①基金进 `\mythanks` 且模板里真能定义 → ②项目号不丢、特殊字符只转义一次
→ ③`null` 与原空值 `{}` 都正确 → ④**不**给英文模板/产物塞 `\mygrants`
→ ⑤来源优先级（显式人工值优先、解析只补空）、基金/致谢并存与去重、
既有 `\mythanks` 值保留 → ⑥通讯作者/单位/邮箱/收稿日期/普通论述脚注不被当基金
→ ⑦中文模板两槽继续分离；无槽位模板零变化；日志项数与实际写入的槽位一致。

P2 七条：①`([18, p. 398])` → `\parencite[p.~398]{键}`，句子与句末标点不丢、
无双括号、只有一处 `\parencite` → ②`p./pp.`、空白、跨 Word run、区间（`- – — ~`）
→ ③编号 18 必须落到源稿**第 18 号条目**（不按字母序/排序猜）→ ④既有
`([18], p. 398)`/`([18])`/`([18,19])`/中文 `（…，第N页）` 行为保持，已有
`\parencite` 不二次包装，脚注通道不重复处理 → ⑤反例：`$…$` 数学、`[18,19]`
多编号、`\url{}`、编号清单 `1. [18]` 不误转；无文献表不猜 → ⑥`--number-cite-map`
默认仍是 False、产物不用 `\nocite` → ⑦`.tex` 里每个新 cite 键都能在交付的
`reference.bib` 里找到。

⚠️ 已知未修（本套件只 `NOTE` 记录、**不**断言为期望行为，交回 lead 派返工）：
`\verb|…|` / `verbatim` / `lstlisting` 里的 `[18, p. 398]` 仍会被替换；
`$$…$$`、`\[…\]`、`\begin{equation}…\end{equation}` 里的也照样替换（只挡了
行内 `$…$`）；`\url{…[18, p. 398]}`、裸 `https://…[18, p. 398]` 同理；
`1. [18, p. 398] 条目`（普通编号列表）也会被替换；自定义骨架**完全没有**
`\mythanks`/`\mygrants` 时，从作者信息脚注取到的基金会被静默丢弃（日志仍打
「基金项目：从作者信息脚注/正文填入…」且无 warning）。

不联网、不写仓库：产物一律进 `tempfile.mkdtemp()`；不跑编译、不碰 corpus。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 \
        tests/test_en_docx_funding_cites_20261116.py
"""

import difflib
import inspect
import os
import re
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from docx_fixture_gen import make_docx            # noqa: E402  共享合成 docx 夹具
from slcn import build, docxsource, metadata_source, templates as T  # noqa: E402
from slcn.footnotes import looks_like_author_info, parse_author_info  # noqa: E402
from slcn.latex import (                          # noqa: E402
    editorial_slots, escape_metadata_value, join_slot_text, live_field_value,
    parse_macros, split_slot_text,
)
from slcn.texfront import TexFrontmatter          # noqa: E402

FAILS: list[str] = []
TMP = tempfile.mkdtemp(prefix="slcn_enfc_")

EN_TPL_PATH = T.resolve("en").skeleton
ZH_TPL_PATH = T.resolve("zh").skeleton
EN_TPL = open(EN_TPL_PATH, encoding="utf-8").read()
ZH_TPL = open(ZH_TPL_PATH, encoding="utf-8").read()
EN_FIELDS = set(parse_macros(EN_TPL))
ZH_FIELDS = set(parse_macros(ZH_TPL))


def check(name: str, cond, detail="") -> None:
    """打一行 `PASS`/`FAIL`（tools/gen_docs.py 按 ^\\s*PASS / ^\\s*FAIL 计数）。"""
    ok = bool(cond)
    line = ("PASS  " if ok else "FAIL  ") + name
    if not ok:
        msg = " ⏎ ".join(str(detail).splitlines()) if detail != "" else ""
        line += ("  " + msg) if msg else ""
        FAILS.append(name)
    print(line)


def note(msg: str) -> None:
    """记一条「已知未修」——不打 PASS/FAIL，故不影响门禁计数。"""
    print("NOTE  " + msg)


def macro(tex: str, name: str):
    """用引擎自己的 `parse_macros()` 取宏值（不自己写正则）；缺失返回 None。"""
    m = parse_macros(tex).get(name)
    return None if m is None else m.value


def one_line_diff(a: str, b: str) -> list[str]:
    d = difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
    return [ln for ln in d if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


def en_tex(meta=None, *, skeleton=None, base="out", grants_note=None):
    """造一份最小英文稿并 build_docx，返回 (BuildResult, tex 文本)。"""
    paras = _en_paras()
    fn = [grants_note] if grants_note else []
    src = os.path.join(TMP, f"{base}.docx")
    make_docx(src, paras=paras, footnotes=fn, footnote_anchor=1 if fn else -1)
    r = build.build_docx(src, TMP, basename=base, lang="en", meta=meta,
                         skeleton=skeleton, compile_requested=False, verbose=False)
    return r, open(r.tex_path, encoding="utf-8").read()


def zh_tex(meta=None, *, grants_note=None, base="zh_out"):
    paras = _zh_paras()
    fn = [grants_note] if grants_note else []
    src = os.path.join(TMP, f"{base}.docx")
    make_docx(src, paras=paras, footnotes=fn, footnote_anchor=1 if fn else -1)
    r = build.build_docx(src, TMP, basename=base, lang="zh", meta=meta,
                         compile_requested=False, verbose=False)
    return r, open(r.tex_path, encoding="utf-8").read()


# 19 条文末编号清单（够覆盖编号 18/19）。条目里带 M18/M19 标记，便于回查 bib 内容。
_REFS = [f"[{i}] Au{i}. Title M{i:02d} marker[J]. Journal of Logic, 20{10 + i % 10}, "
         f"1(1): {i}--{i + 5}." for i in range(1, 20)]


def _en_paras(body="Body ([18, p. 398]) end."):
    return ["测试题名：合成英文题名", "Zhang San", "摘要：Synthetic abstract.",
            "关键词：test", body, "参考文献"] + list(_REFS)


def _zh_paras(body="正文（[18, p. 398]）结束。"):
    return ["测试题名：合成中文题名", "张三", "摘要：合成摘要。", "关键词：测试",
            body, "参考文献"] + list(_REFS)


def _no_ref_paras():
    return ["测试题名：无文献表", "Zhang San", "摘要：Synthetic abstract.",
            "关键词：test", "Body ([18, p. 398]) end."]


def _skeleton(orig: str, *, mythanks=None, drop=None) -> str:
    """改英文骨架的 `\\mythanks` 定义行：换值 / 换宏名 / 整行删掉。

    注意模板那行带行尾注释（`...{null}   % Insert grants, ...`），所以按**行首**匹配
    而不是按 `^...\\}$` 匹配。
    """
    if mythanks is None and drop is None:
        return orig
    if drop is not None:
        head = "\\newcommand{\\my" + drop + "}"
        return "\n".join(ln for ln in orig.splitlines() if not ln.startswith(head)) + "\n"
    pat = r"\\newcommand\{\\my(?:thanks|grants)\}\{[^\n]*?\}(?:\s*%.*)?$"
    return re.sub(pat, "\\\\newcommand{\\\\my" + mythanks[0] + "}{" + mythanks[1] + "}",
                  orig, count=1, flags=re.M)


# ══════════════════════════════════════════════════════════════════════════
print("── P1 ① 英文模板单槽：基金必须落进 `\\mythanks`（而不是不存在的 `\\mygrants`）──")
# ══════════════════════════════════════════════════════════════════════════

check("P1① 英文模板里**没有** `\\newcommand{\\mygrants}`（基金唯一的落脚点是 mythanks）",
      "\\newcommand{\\mygrants}" not in EN_TPL,
      EN_TPL_PATH)
check("P1① `editorial_slots(EN)` 把 grants 与 thanks 都指向 `mythanks`",
      editorial_slots(EN_FIELDS) == {"grants": "mythanks", "thanks": "mythanks"},
      editorial_slots(EN_FIELDS))
check("P1① 中文模板两槽都在，`editorial_slots(ZH)` 保持 mygrants/mythanks 分离",
      "\\newcommand{\\mygrants}" in ZH_TPL and "\\newcommand{\\mythanks}" in ZH_TPL
      and editorial_slots(ZH_FIELDS) == {"grants": "mygrants", "thanks": "mythanks"},
      editorial_slots(ZH_FIELDS))
check("P1① 只有 mythanks 的模板 → 两字段同槽；只有 mytitle 的模板 → 两字段皆空",
      editorial_slots({"mythanks"}) == {"grants": "mythanks", "thanks": "mythanks"}
      and editorial_slots({"mytitle"}) == {"grants": "", "thanks": ""},
      (editorial_slots({"mythanks"}), editorial_slots({"mytitle"})))

_t, _w = build.apply_editorial_fields(EN_TPL, {"grants": "国家社科基金项目 20BZX001"},
                                     tpl_fields=EN_FIELDS)
check("P1① 仅基金 → 写入 `mythanks`，wrote 只有一项",
      _w == ["mythanks"] and macro(_t, "mythanks") == "国家社科基金项目 20BZX001。",
      (_w, macro(_t, "mythanks")))
check("P1① 写入后产物里**没有** `\\mygrants` 定义（不给英文稿塞无效宏）",
      "\\newcommand{\\mygrants}" not in _t, "")
check("P1① SLEN.sty 真的把 `\\mythanks` 排成标题 `\\thanks` 脚注（模板侧可用）",
      re.search(r"\\equal\{\\mythanks\}\{null\}", _sty_text := open(
          T.resolve("en").sty, encoding="utf-8").read()) is not None
      and "\\thanks{\\mythanks}" in _sty_text, "SLEN.sty 未见 \\thanks{\\mythanks}")

# ══════════════════════════════════════════════════════════════════════════
print("── P1 ② 项目号不丢、TeX 特殊字符只转义一次 ──")
# ══════════════════════════════════════════════════════════════════════════

check("P1② 特殊字符 `50% & #1_2` 被转义成 `50\\% \\& \\#1\\_2`",
      escape_metadata_value("50% & #1_2") == "50\\% \\& \\#1\\_2",
      escape_metadata_value("50% & #1_2"))
_r_esc, _tex_esc = en_tex(grants_note="作者简介：张三\n单位：示例大学\n邮箱：zs@example.edu.cn\n"
                                     "基金项目：国家社科基金 50% & 项目#1_2")
_v_esc = macro(_tex_esc, "mythanks")
check("P1② 端到端：特殊字符进入 mythanks 且只转义一次（无 `\\\\%` 二次转义）",
      _v_esc == "国家社科基金 50\\% \\& 项目\\#1\\_2。" and "\\\\%" not in _v_esc,
      repr(_v_esc))
_clean, _cw = build.clean_field_value("mygrants", "国家社科基金（项目编号：20BZX001）")
check("P1② `（项目编号：20BZX001）` 去掉引导语但项目号 20BZX001 原样保留",
      _clean == "国家社科基金（20BZX001）。" and "20BZX001" in _clean, repr(_clean))
_t2, _ = build.apply_editorial_fields(EN_TPL, {"grants": "A基金（20BZX001）"},
                                     tpl_fields=EN_FIELDS)
check("P1② 括注里的项目号一个字符都不丢", "20BZX001" in (macro(_t2, "mythanks") or ""),
      macro(_t2, "mythanks"))

# ══════════════════════════════════════════════════════════════════════════
print("── P1 ③ 模板原值 `null` 与原空值 `{}` 都要能正确填 ──")
# ══════════════════════════════════════════════════════════════════════════

check("P1③ 原值 `null` 被 `live_field_value()` 判为空（不是字面量 'null'）",
      live_field_value(EN_TPL, "mythanks") == "", repr(live_field_value(EN_TPL, "mythanks")))
check("P1③ 模板里没有的宏，`live_field_value()` 也返回空（不抛异常）",
      live_field_value(EN_TPL, "mygrants") == "", repr(live_field_value(EN_TPL, "mygrants")))
_sk_empty = _skeleton(EN_TPL, mythanks=("thanks", ""))
_skp_empty = os.path.join(TMP, "skel_empty.tex")
open(_skp_empty, "w", encoding="utf-8").write(_sk_empty)
_r_e, _tex_e = en_tex({"grants": "A基金 9"}, skeleton=_skp_empty, base="skel_empty_out")
check("P1③ 原值改成空 `{}` 后仍能填入（空槽 ≠ 跳过）",
      macro(_tex_e, "mythanks") == "A基金 9。", repr(macro(_tex_e, "mythanks")))
_tn, _wn = build.apply_editorial_fields(EN_TPL, {}, tpl_fields=EN_FIELDS)
check("P1③ 空 meta → 模板逐字节不变、wrote 为空（null 不会被改写成别的东西）",
      _tn == EN_TPL and _wn == [], (_wn, _tn == EN_TPL))
_tz, _wz = build.apply_editorial_fields(ZH_TPL, {}, tpl_fields=ZH_FIELDS)
check("P1③ 中文模板空 meta → 逐字节不变、wrote 为空",
      _tz == ZH_TPL and _wz == [], (_wz, _tz == ZH_TPL))

# ══════════════════════════════════════════════════════════════════════════
print("── P1 ④ 不修改英文模板、不向交付的英文 TeX 注入 `\\mygrants` ──")
# ══════════════════════════════════════════════════════════════════════════

check("P1④ 磁盘上的 EN_template.tex 仍只有 mythanks（模板文件零改动）",
      "\\mygrants" not in EN_TPL, EN_TPL_PATH)
_r4, _tex4 = en_tex({"grants": "国家社科基金 20BZX001"}, base="p1_4")
check("P1④ 端到端交付的 .tex 里既无 `\\mygrants` 定义也无 `\\mygrants` 引用",
      "mygrants" not in _tex4, "")
check("P1④ `\\nocite` 未被引入（不靠全量打印掩盖）", "\\nocite" not in _tex4, "")

# ══════════════════════════════════════════════════════════════════════════
print("── P1 ⑤ 来源优先级 / 基金与致谢并存 / 既有值保全 / 去重 ──")
# ══════════════════════════════════════════════════════════════════════════

_r5, _tex5 = en_tex(grants_note="基金项目：A基金 111", base="p1_5a")
check("P1⑤ 仅基金（作者信息脚注来源）→ mythanks 只有基金",
      macro(_tex5, "mythanks") == "A基金 111。", repr(macro(_tex5, "mythanks")))
_r5b, _tex5b = en_tex({"thanks": "感谢张三的讨论。"}, base="p1_5b")
check("P1⑤ 仅致谢（显式 meta）→ mythanks 原样是致谢",
      macro(_tex5b, "mythanks") == "感谢张三的讨论。", repr(macro(_tex5b, "mythanks")))
_r5c, _tex5c = en_tex({"grants": "A基金 111", "thanks": "感谢张三的讨论。"}, base="p1_5c")
check("P1⑤ 基金 + 致谢并存 → 同一 mythanks 内拼接，致谢在前、基金在后",
      macro(_tex5c, "mythanks") == "感谢张三的讨论；A基金 111。",
      repr(macro(_tex5c, "mythanks")))
_r5d, _tex5d = en_tex({"grants": "B基金 222"}, grants_note="基金项目：A基金 111", base="p1_5d")
check("P1⑤ 显式人工值优先：meta 的 B基金 覆盖源稿解析出的 A基金",
      macro(_tex5d, "mythanks") == "B基金 222。", repr(macro(_tex5d, "mythanks")))
_t5, _w5 = build.apply_editorial_fields(EN_TPL, {"grants": "A基金", "thanks": "A基金"},
                                       tpl_fields=EN_FIELDS)
check("P1⑤ 基金与致谢同文本 → 去重成一句（不出现 `A基金；A基金。`）",
      macro(_t5, "mythanks") == "A基金。", repr(macro(_t5, "mythanks")))
check("P1⑤ `split_slot_text` 按 `；;。\\n` 切句并丢掉 null/空段",
      split_slot_text("A；B。C\nnull; D") == ["A", "B", "C", "D"],
      split_slot_text("A；B。C\nnull; D"))
check("P1⑤ `join_slot_text` 去重、用「；」连接、「。」收尾；全空返回空串",
      join_slot_text("A基金", "A基金") == "A基金。"
      and join_slot_text("谢Y", "A基金") == "谢Y；A基金。"
      and join_slot_text() == "" and join_slot_text("null", None, "  ") == "",
      (join_slot_text("A基金", "A基金"), join_slot_text(), join_slot_text("null", None, "  ")))
_sk_pre = _skeleton(EN_TPL, mythanks=("thanks", "既有致谢 X"))
_skp_pre = os.path.join(TMP, "skel_pre.tex")
open(_skp_pre, "w", encoding="utf-8").write(_sk_pre)
_r5e, _tex5e = en_tex({"grants": "A基金 20BZX001"}, skeleton=_skp_pre, base="p1_5e")
check("P1⑤ 模板已有有效 mythanks（既有致谢 X）→ 保留并只在后面追加基金",
      macro(_tex5e, "mythanks") == "既有致谢 X；A基金 20BZX001。",
      repr(macro(_tex5e, "mythanks")))
check("P1⑤ 重复调用幂等（第二次不再追加、不重复拼接）",
      (lambda a: macro(build.apply_editorial_fields(
          a, {"grants": "A基金", "thanks": "谢Y"}, tpl_fields=set(parse_macros(a)))[0],
          "mythanks") == "谢Y；A基金。")(
          build.apply_editorial_fields(EN_TPL, {"grants": "A基金", "thanks": "谢Y"},
                                       tpl_fields=EN_FIELDS)[0]), "")

# ══════════════════════════════════════════════════════════════════════════
print("── P1 ⑥ 通讯作者/单位/邮箱/收稿日期/普通论述脚注都不许当基金 ──")
# ══════════════════════════════════════════════════════════════════════════

_CORR = parse_author_info("作者信息：张三（通讯作者）\n单位：示例大学哲学系\n邮箱：zs@example.edu.cn")
check("P1⑥ 通讯作者 + 单位 + 邮箱脚注 → grants 为空",
      _CORR is not None and _CORR.get("grants") == "", _CORR and _CORR.get("grants"))
_RECV = parse_author_info("作者信息：张三\n单位：示例大学哲学系\n邮箱：zs@example.edu.cn\n"
                          "收稿日期：2026-03-04")
check("P1⑥ 收稿日期脚注 → grants 为空、收稿日期照常识别",
      _RECV is not None and _RECV.get("grants") == "" and _RECV.get("received") == "2026-03-04",
      _RECV)
check("P1⑥ 普通论述脚注（致谢语句）不被认成作者信息（因此也不会当基金）",
      looks_like_author_info("本文写作过程中得到张三的许多帮助，特此致谢。") is False
      and parse_author_info("本文写作过程中得到张三的许多帮助，特此致谢。") is None, "")
_ML = parse_author_info("作者简介：张三\n单位：示例大学哲学系\n邮箱：zs@example.edu.cn\n"
                        "基金项目：国家社科基金 20BZX001")
check("P1⑥ 真带「基金项目：」的多行作者信息 → grants 正确抽出（正例对照）",
      _ML is not None and _ML.get("grants") == "国家社科基金 20BZX001", _ML)
_rn, _texn = en_tex(base="p1_6_none")
check("P1⑥ 没有基金/致谢的英文稿 → mythanks 仍是 null，不写任何占位文字",
      macro(_texn, "mythanks") == "null", repr(macro(_texn, "mythanks")))
check("P1⑥ 无基金稿的日志说「出版信息未提供」，不谎报基金来源",
      any("出版信息未提供" in ln for ln in _rn.log)
      and not any("基金项目：从作者信息" in ln for ln in _rn.log),
      [ln for ln in _rn.log if "出版信息" in ln or "基金" in ln])

# ══════════════════════════════════════════════════════════════════════════
print("── P1 ⑦ 中文两槽分离 / 无槽位零变化 / 日志与实际 TeX 一致 ──")
# ══════════════════════════════════════════════════════════════════════════

_tz7, _wz7 = build.apply_editorial_fields(ZH_TPL, {"grants": "国家社科基金 20BZX001"},
                                          tpl_fields=ZH_FIELDS)
check("P1⑦ 中文模板仅基金 → 只写 mygrants，mythanks 保持 null（两槽不混）",
      _wz7 == ["mygrants"] and macro(_tz7, "mygrants") == "国家社科基金 20BZX001"
      and macro(_tz7, "mythanks") == "null", (_wz7, macro(_tz7, "mygrants"),
                                              macro(_tz7, "mythanks")))
_tz7b, _wz7b = build.apply_editorial_fields(ZH_TPL, {"grants": "G基金", "thanks": "谢Y"},
                                            tpl_fields=ZH_FIELDS)
check("P1⑦ 中文模板基金 + 致谢并存 → 两个槽各写各的（不合并成一句）",
      _wz7b == ["mygrants", "mythanks"] and macro(_tz7b, "mygrants") == "G基金"
      and macro(_tz7b, "mythanks") == "谢Y", (_wz7b, macro(_tz7b, "mygrants"),
                                             macro(_tz7b, "mythanks")))
_rz, _texz = zh_tex(grants_note="基金项目：A基金 111", base="p1_7_zh")
check("P1⑦ 中文端到端：基金落 mygrants、mythanks 仍 null",
      "A基金 111" in (macro(_texz, "mygrants") or "") and macro(_texz, "mythanks") == "null",
      (macro(_texz, "mygrants"), macro(_texz, "mythanks")))
check("P1⑦ 中文端到端日志项数与实际写入的槽位一致（只 1 项 mygrants）",
      any("填入出版信息 1 项：mygrants" in ln for ln in _rz.log),
      [ln for ln in _rz.log if "出版信息" in ln])
_rz_n, _tzn = zh_tex(base="p1_7_zh_none")
check("P1⑦ 无基金的中文稿 → 两槽都保持 null（中文通道零意外变化）",
      macro(_tzn, "mygrants") == "null" and macro(_tzn, "mythanks") == "null", "")
_sk_none = _skeleton(EN_TPL, drop="thanks")
_skp_none = os.path.join(TMP, "skel_noslot.tex")
open(_skp_none, "w", encoding="utf-8").write(_sk_none)
_t7c, _w7c = build.apply_editorial_fields(_sk_none, {"grants": "A基金", "thanks": "谢Y"},
                                          tpl_fields=set(parse_macros(_sk_none)))
check("P1⑦ 自定义模板一个槽都没有 → wrote 为空、模板逐字节不变、不虚报写成功",
      _w7c == [] and _t7c == _sk_none, (_w7c, _t7c == _sk_none))
_t7g, _w7g = build.apply_editorial_fields(
    EN_TPL, {"grants": "G", "thanks": "谢", "vol": "12", "issue": "3", "year": "2026",
             "pages": "137-152", "received": "2026-03-04", "editor": "李四"},
    tpl_fields=EN_FIELDS)
_changed = {k for k in EN_FIELDS if macro(_t7g, k) != macro(EN_TPL, k)}
check("P1⑦ 日志项数只数真正改动的槽：wrote == 模板里值确实变了的宏集合（不多不少）",
      set(_w7g) == _changed and len(_w7g) == len(set(_w7g)) and _w7g,
      (_w7g, sorted(_changed)))
_t7f, _w7f = build.apply_editorial_fields(EN_TPL, {"grants": "G", "thanks": "谢"},
                                          tpl_fields=EN_FIELDS)
check("P1⑦ 基金+致谢（EN 单槽）只记 1 项 mythanks，绝不虚报 2 项",
      _w7f == ["mythanks"], _w7f)
_r7d, _tex7d = en_tex({"grants": "国家社科基金 20BZX001"}, base="p1_7_d")
check("P1⑦ 端到端日志「填入出版信息 1 项：mythanks」与实际 TeX 一致",
      any("填入出版信息 1 项：mythanks" in ln for ln in _r7d.log)
      and macro(_tex7d, "mythanks") == "国家社科基金 20BZX001。",
      [ln for ln in _r7d.log if "出版信息" in ln])
_r7e, _tex7e = en_tex(base="p1_7_e")
check("P1⑦ 无基金稿 与 有基金稿 的英文 .tex 只差 `\\mythanks` 那一行（无关通道零变化）",
      len(one_line_diff(_tex7e, _tex7d)) == 2, one_line_diff(_tex7e, _tex7d))
check("P1⑦ `metadata_source`：EN 的 grants/thanks 同指 mythanks，mygrants 仅作兼容别名",
      metadata_source._CANON_MACRO["en"]["grants"] == "mythanks"
      and metadata_source._CANON_MACRO["en"]["thanks"] == "mythanks"
      and metadata_source._MACRO_CANON["en"].get("mygrants") == "grants"
      and metadata_source._CANON_MACRO["zh"]["grants"] == "mygrants", "")
_src7 = os.path.join(TMP, "src7.tex")
open(_src7, "w", encoding="utf-8").write(
    "\\newcommand{\\mygrants}{国家社科基金 20BZX001}\n"
    "\\newcommand{\\mythanks}{感谢张三的讨论}\n")
_p7, _n7, _w7 = metadata_source.compose_latex([_src7], lang="en")
_lines7 = [ln for ln in open(_p7, encoding="utf-8").read().splitlines()
           if "mythanks" in ln or "mygrants" in ln]
check("P1⑦ `compose_latex(EN)` 把 grants+thanks 合成**一行** mythanks，不产出 mygrants",
      len(_lines7) == 1 and _lines7[0].startswith("\\newcommand{\\mythanks}{")
      and "感谢张三的讨论；国家社科基金 20BZX001。" in _lines7[0], _lines7)
_p7z, _n7z, _w7z = metadata_source.compose_latex([_src7], lang="zh")
_lines7z = [ln for ln in open(_p7z, encoding="utf-8").read().splitlines() if "my" in ln]
check("P1⑦ `compose_latex(ZH)` 仍输出 mygrants/mythanks 两行（中文不合并）",
      len([ln for ln in _lines7z if "mygrants" in ln or "mythanks" in ln]) == 2, _lines7z)
_fm = TexFrontmatter(title="T", grants="国家社科基金 20BZX001", thanks="感谢张三的讨论")
_v_en = {k: v for k, v in _fm.template_values(EN_FIELDS).items() if "thank" in k or "grant" in k}
_v_zh = {k: v for k, v in _fm.template_values(ZH_FIELDS).items() if "thank" in k or "grant" in k}
check("P1⑦ `TexFrontmatter.template_values(EN)` 只落 mythanks（合并去重）",
      _v_en == {"mythanks": "感谢张三的讨论；国家社科基金 20BZX001。"}, _v_en)
check("P1⑦ `TexFrontmatter.template_values(ZH)` 两槽分离",
      _v_zh == {"mygrants": "国家社科基金 20BZX001", "mythanks": "感谢张三的讨论"}, _v_zh)
_body7 = os.path.join(TMP, "body7.tex")
open(_body7, "w", encoding="utf-8").write("\\section{Intro}\nText ([18, p. 398]) here.\n")
_meta7 = os.path.join(TMP, "meta7.tex")
open(_meta7, "w", encoding="utf-8").write("\\newcommand{\\mygrants}{国家社科基金 20BZX001}\n")
_rl = build.build_latex(_body7, TMP, basename="tex7", lang="en", metadata_path=_meta7,
                        verbose=False)
_texl = open(_rl.tex_path, encoding="utf-8").read()
check("P1⑦ LaTeX 通道：源 .tex 的 `\\mygrants` 也并进产物的 mythanks（不产出 mygrants）",
      macro(_texl, "mythanks") == "国家社科基金 20BZX001。" and "mygrants" not in _texl,
      macro(_texl, "mythanks"))
_rl0 = build.build_latex(_body7, TMP, basename="tex7b", lang="en", verbose=False)
_texl0 = open(_rl0.tex_path, encoding="utf-8").read()
check("P1⑦ LaTeX 通道无元数据时 mythanks 仍是 null（零意外变化）",
      macro(_texl0, "mythanks") == "null", repr(macro(_texl0, "mythanks")))

# P1⑦ 返工回归（原「已知未修」NOTE 已失效，task-10 已修）：无槽骨架 + 有标签基金脚注
# → 必须如实告警「未写入 / 请人工处理」，值不落盘，日志不再谎报「填入」。
_skyl = _skeleton(EN_TPL, drop="thanks")          # EN 本无 mygrants，删 mythanks → 零槽
_skylp = os.path.join(TMP, "skel_noslot_warn.tex")
open(_skylp, "w", encoding="utf-8").write(_skyl)
_rl1, _texl1 = en_tex(grants_note="基金项目：国家社科基金 20BZX001",
                      skeleton=_skylp, base="p1_7_noslot_warn")
check("P1⑦（已修）无槽骨架+作者信息脚注基金 → warning 明说未写入、请人工处理",
      any("模板没有基金项目槽位" in w and "未写入" in w and "请人工处理" in w
          for w in _rl1.warnings),
      _rl1.warnings)
check("P1⑦（已修）无槽骨架 → 基金值不在产物 .tex（不静默填入、不落盘）",
      "国家社科基金 20BZX001" not in _texl1, "")
check("P1⑦（已修）日志只说「解析到/出版信息未提供」，不再谎报「…正文填入」",
      not any("基金项目：从作者信息脚注/正文填入" in ln for ln in _rl1.log)
      and any("解析到" in ln and "基金" in ln for ln in _rl1.log)
      and any("出版信息未提供" in ln for ln in _rl1.log),
      [ln for ln in _rl1.log if "基金" in ln or "出版信息" in ln])

# ══════════════════════════════════════════════════════════════════════════
print("── P2 ① `([18, p. 398])` → `\\parencite[p.~398]{键}`：句子/标点/页码都在 ──")
# ══════════════════════════════════════════════════════════════════════════

N2K = {18: "K18", 19: "K19", 2: "K2", 6: "K6"}


def cite(body, lang="en", n2k=None):
    return docxsource.link_numbered_citations(body, N2K if n2k is None else n2k, lang)


_out, _n, _iss = cite("See ([18, p. 398]).")
check("P2① `See ([18, p. 398]).` → `See \\parencite[p.~398]{K18}.`（句末句号保留）",
      _out == "See \\parencite[p.~398]{K18}." and _n == 1 and not _iss, repr(_out))
check("P2① 结果里没有双括号 `((`、`))`，也没有重复的 `\\parencite`",
      "((" not in _out and "))" not in _out and _out.count("\\parencite") == 1, repr(_out))
_zh1, _nzh1, _ = cite("见（[18]，第398页）。", lang="zh")
check("P2① 中文形态 `（[18]，第398页）` → `（\\parencite{K18}，第398页）`（页码不丢）",
      _zh1 == "见（\\parencite{K18}，第398页）。" and _nzh1 == 1, repr(_zh1))

# ══════════════════════════════════════════════════════════════════════════
print("── P2 ② p./pp.、空白、区间（- – — ~）、全角逗号都要认 ──")
# ══════════════════════════════════════════════════════════════════════════

for _src, _want, _label in [
    ("See ([18, pp. 398–401]).", "See \\parencite[pp.~398--401]{K18}.", "en dash 区间"),
    ("See ([18, pp.398-401]).", "See \\parencite[pp.~398--401]{K18}.", "无空格 + 半角连字符"),
    ("See ([18, p. 398~401]).", "See \\parencite[pp.~398--401]{K18}.", "`~` 作区间符"),
    ("See ([18, pp. 398—401]).", "See \\parencite[pp.~398--401]{K18}.", "em dash 区间"),
    ("See ([18,p.398]).", "See \\parencite[p.~398]{K18}.", "全无空格"),
    ("See ( [18, p. 398] ).", "See \\parencite[p.~398]{K18}.", "括号内左右空白"),
]:
    _o, _nn, _ii = cite(_src)
    check(f"P2② {_label} → `\\parencite[pp.~…--…]`（区间统一成双短横）",
          _o == _want and _nn == 1, repr(_o))
_o_fw, _n_fw, _ = cite("See ([18，p. 398]).")
check("P2② 全角逗号 `[18，p. 398]` 也认（Word 中文输入法常见）",
      _o_fw == "See \\parencite[p.~398]{K18}." and _n_fw == 1, repr(_o_fw))
_o_zh_fw, _n_zh_fw, _ = cite("见（[18，第398页]）。", lang="zh")
check("P2② 中文 `第N页` 写在方括号里也认", _o_zh_fw == "见（\\parencite{K18}，第398页）。",
      repr(_o_zh_fw))

# 跨 Word run：docx 层把 `([`+`18`+`, p. `+`398`+`])` 拆成 5 个 run，解析后应合并成一句
import zipfile  # noqa: E402
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p_runs(runs):
    return "<w:p>" + "".join(f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>'
                             for t in runs) + "</w:p>"


_sr = os.path.join(TMP, "splitrun.docx")
_paras = [_p_runs(["测试题名：跨 run 合成"]), _p_runs(["Zhang San"]),
          _p_runs(["摘要：Synthetic abstract."]), _p_runs(["关键词：test"]),
          _p_runs(["Body ([", "18", ", p. ", "398", "]) end."]),
          _p_runs(["参考文献"])] + [_p_runs([r]) for r in _REFS]
with zipfile.ZipFile(_sr, "w", zipfile.ZIP_DEFLATED) as _z:
    _z.writestr("word/document.xml",
                f"<w:document {_W}><w:body>{''.join(_paras)}</w:body></w:document>")
_dsr = docxsource.parse_docx(_sr, c_class_union=True)
check("P2② 跨 run 的 `([18, p. 398])` 在解析层被合并成一句（空白不丢）",
      _dsr.body.strip() == "Body ([18, p. 398]) end.", repr(_dsr.body))
_rsr = build.build_docx(_sr, TMP, basename="splitrun", lang="en",
                        compile_requested=False, verbose=False)
_texsr = open(_rsr.tex_path, encoding="utf-8").read()
check("P2② 跨 run 端到端也转成 `\\parencite[p.~398]{…}`",
      "Body (\\parencite[p.~398]{" in _texsr
      and "([18, p. 398])" not in _texsr,
      [ln for ln in _texsr.splitlines() if "Body" in ln])

# ══════════════════════════════════════════════════════════════════════════
print("── P2 ③ 编号 18 必须落到源稿第 18 号条目（不按字母序/排序猜）──")
# ══════════════════════════════════════════════════════════════════════════

_AUTHORS = ["An", "Bo", "Cai", "Du", "Fan", "Guo", "He", "Jin", "Kong", "Lu",
            "Ma", "Nie", "Ou", "Pan", "Qiu", "Ren", "Song", "Tang", "Zuo"]
_map_refs = []
for _i in range(1, 20):
    # 故意让第 18 条作者字母序**最后**（Zuo）、第 19 条**最前**（An）：
    # 若实现按「排序后第 18 项」猜，就会拿错条目。
    _au = "Zuo" if _i == 18 else ("An" if _i == 19 else _AUTHORS[(_i - 1) % len(_AUTHORS)])
    _map_refs.append(f"[{_i}] {_au} Author. Title M{_i:02d} marker[J]. "
                     f"Journal of Logic, 20{10 + _i % 10}, 1(1): {_i}--{_i + 5}.")
_map_docx = os.path.join(TMP, "keymap.docx")
make_docx(_map_docx, paras=["测试题名：键映射", "Zhang San", "摘要：Synthetic abstract.",
                            "关键词：test",
                            "Body ([18, p. 398]) and ([19, p. 5]).", "参考文献"] + _map_refs)
_md = docxsource.parse_docx(_map_docx, c_class_union=True)
_key18 = _md.ref_key_by_num.get(18)
_key19 = _md.ref_key_by_num.get(19)
_rm = build.build_docx(_map_docx, TMP, basename="keymap", lang="en",
                       compile_requested=False, verbose=False)
_texm = open(_rm.tex_path, encoding="utf-8").read()
_bibm = open(_rm.bib_path, encoding="utf-8").read() if _rm.bib_path else ""
check("P2③ 编号 18 → 第 18 号条目的键（键名里带 2018，不是 2019）",
      _key18 == "Logic2018a" and _key19 == "Logic2019a", (_key18, _key19))
check("P2③ `.tex` 的 18 号引用用的是第 18 条目的键，19 号是第 19 条目的键",
      f"\\parencite[p.~398]{{{_key18}}}" in _texm
      and f"\\parencite[p.~5]{{{_key19}}}" in _texm, _texm[:0])
_m18 = re.search(r"@\w+\{" + re.escape(_key18) + r",(.*?)\n\}", _bibm, re.S)
_m19 = re.search(r"@\w+\{" + re.escape(_key19) + r",(.*?)\n\}", _bibm, re.S)
check("P2③ bib 里该键的正文确实是 M18 条目（不是 M19，也不是空条目）",
      _m18 is not None and "M18" in _m18.group(1) and "M19" not in _m18.group(1),
      (_m18.group(1)[:80] if _m18 else None))
check("P2③ 19 号键的条目是 M19（两条编号没有互相串位）",
      _m19 is not None and "M19" in _m19.group(1) and "M18" not in _m19.group(1),
      (_m19.group(1)[:80] if _m19 else None))

# ══════════════════════════════════════════════════════════════════════════
print("── P2 ④ 既有行为保持；`[18,19]` 不许拆；已有 cite/脚注通道不重复处理 ──")
# ══════════════════════════════════════════════════════════════════════════

_o, _n, _ = cite("See ([18], p. 398).")
check("P2④ 既有 `([18], p. 398)`（定位在方括号外）仍转成 `[p.~398]`",
      _o == "See \\parencite[p.~398]{K18}." and _n == 1, repr(_o))
_o, _n, _ = cite("See ([18]).")
check("P2④ 既有 `([18])` 仍是 `(\\parencite{K18})`（外层圆括号不丢）",
      _o == "See (\\parencite{K18})." and _n == 1, repr(_o))
_o, _n, _ = cite("See ([18,19]).")
check("P2④ 多编号 `([18,19])` → 一个命令 `\\parencite{K18,K19}`（不拆成两条）",
      _o == "See (\\parencite{K18,K19})." and _n == 1 and _o.count("\\parencite") == 1,
      repr(_o))
_o_en, _n_en, _ = cite("See [18,19].")
_o_zh, _n_zh, _ = cite("See [18,19].", lang="zh")
check("P2④ 裸 `[18,19]`（无括号）**绝不**被拆成「编号 18 + 页码 19」",
      _o_en == "See \\parencite{K18,K19}." and _n_en == 1
      and _o_zh == "See [18,19]." and _n_zh == 0, (repr(_o_en), repr(_o_zh)))
_o, _n, _ = cite("Already \\parencite[p.~398]{KeyX} here.")
check("P2④ 已有 `\\parencite` 命令不再包一层（幂等，n=0）",
      _o == "Already \\parencite[p.~398]{KeyX} here." and _n == 0, repr(_o))
_o, _n, _ = cite("\\footnote{See [18, p. 398].}")
check("P2④ 脚注参数里的 `([18, p. 398])` 不在正文通道重复处理（n=0，原样）",
      _o == "\\footnote{See [18, p. 398].}" and _n == 0, repr(_o))
_o, _n, _ = cite("see \\footnote{note} [18, p. 398]")
check("P2④ 脚注**之后**的正文引用照常转（不因前面有 footnote 就整段跳过）",
      _n == 1 and "\\parencite[p.~398]{K18}" in _o, repr(_o))
_o2, _n2, _ = cite(_o)
check("P2④ 二次处理幂等：已转换文本再跑一遍 n=0 且逐字节不变",
      _n2 == 0 and _o2 == _o, repr(_o2))

# ══════════════════════════════════════════════════════════════════════════
print("── P2 ⑤ 反例：数学/多编号/URL/编号清单/无文献表 → 不许误转 ──")
# ══════════════════════════════════════════════════════════════════════════

for _src, _label in [
    ("$[18,19]$", "行内数学区间"),
    ("$\\vec a=[18,19]$", "行内数学向量"),
    ("$[18, p. 398]$", "行内数学里带 p. 的方括号"),
    ("见 $[18, 19]$ 区间", "中文行内数学区间"),
    ("\\begin{align} a=[18, 19] \\end{align}", "align 环境里的区间"),
]:
    for _lang in ("en", "zh"):
        _o, _n, _ = cite(_src, lang=_lang)
        check(f"P2⑤ {_label}（{_lang}）原样保留、n=0", _o == _src and _n == 0, repr(_o))
for _src in ("\\url{https://example.org/a[18]}",
             "\\url{https://example.org/x?y=[18]}"):
    _o, _n, _ = cite(_src)
    check(f"P2⑤ URL 里的 `[18]` 不转：{_src[:32]}…", _o == _src and _n == 0, repr(_o))
for _src in ("1. [18] item one", "1. [18] 条目一", "[18] 张三. 题名[J]. 期刊, 2020."):
    _o, _n, _ = cite(_src, lang="zh")
    check(f"P2⑤ 普通编号清单项 `{_src[:18]}…` 原样保留（zh，n=0）", _o == _src and _n == 0,
          repr(_o))
_o_en_list, _n_en_list, _ = cite("1. [18] item one")
check("P2⑤ 纯编号清单项（en）不因 `_link_bare_paged_citations` 被误转",
      _n_en_list == 0, repr(_o_en_list))
_o, _n, _iss = cite("See [99] out of range.")
check("P2⑤ 编号越界 `[99]` → 原样保留并报一条「超出」问题（不猜键）",
      "[99]" in _o and _n == 0 and any("超出" in x for x in _iss), (_o, _iss))
_o, _n, _ = cite("Body ([18, p. 398]) end.", n2k={})
check("P2⑤ 拿不到可靠编号→键映射（空表）→ 一个字都不改、也不报 issue",
      _o == "Body ([18, p. 398]) end." and _n == 0, repr(_o))
_noref = os.path.join(TMP, "noref.docx")
make_docx(_noref, paras=_no_ref_paras())
_rnr = build.build_docx(_noref, TMP, basename="noref", lang="en",
                        compile_requested=False, verbose=False)
_texnr = open(_rnr.tex_path, encoding="utf-8").read()
check("P2⑤ 无参考文献表的稿：正文 `[18, p.` 原样（不猜键），并给出「未解析出参考文献」警告",
      "[18, p." in _texnr and any("未解析出参考文献" in w for w in _rnr.warnings),
      [w[:60] for w in _rnr.warnings])

# ══════════════════════════════════════════════════════════════════════════
print("── R1 返工：受保护区间（组通道 `repl()` 也必须吃 `_unwritable_mask()`）──")
# ══════════════════════════════════════════════════════════════════════════
# R1 实证阻断缺陷：组通道 grp.sub(repl) 漏用掩码，9 组探针里 6 个受保护反例被误转。
# 下列断言照 lead-review/probe_protected_groups.py 的 9 个匿名用例逐字固定：
# 受保护样本必须逐字节原样、n=0；两条正向必须仍各转 1 处。
# 任一受保护样本被改写 → FAIL → rc≠0（不再只打 NOTE）。
_R1_PROTECTED = [
    (r"\verb|[18, p. 398]|", r"\verb|[18, p. 398]|", "verb 裸编号"),
    (r"\verb|([18, p. 398])|", r"\verb|([18, p. 398])|", "verb 圆括号组"),
    ("% ([18, p. 398])\nText", "% ([18, p. 398])\nText", "% 注释里的圆括号组"),
    (r"\url{https://example.invalid/([18, p. 398])}",
     r"\url{https://example.invalid/([18, p. 398])}", "URL 参数里的圆括号组"),
    (r"\footnote{([18, p. 398])}", r"\footnote{([18, p. 398])}", "脚注参数里的圆括号组"),
    (r"\begin{verbatim}([18, p. 398])\end{verbatim}",
     r"\begin{verbatim}([18, p. 398])\end{verbatim}", "verbatim 环境里的圆括号组"),
    (r"\parencite[([18, p. 398])]{K19}",
     r"\parencite[([18, p. 398])]{K19}", "已有 \\parencite[...] 可选参不套第二层"),
    # 同类反例（同一掩码判据）：
    (r"\begin{lstlisting}([18, p. 398])\end{lstlisting}",
     r"\begin{lstlisting}([18, p. 398])\end{lstlisting}", "lstlisting 环境"),
    (r"\begin{equation}([18, p. 398])\end{equation}",
     r"\begin{equation}([18, p. 398])\end{equation}", "equation 环境"),
    ("$$([18, p. 398])$$", "$$([18, p. 398])$$", "行间公式 $$…$$"),
    (r"\[([18, p. 398])\]", r"\[([18, p. 398])\]", "行间公式 \\[\…\\]"),
    (r"\href{https://example.invalid/([18, p. 398])}{x}",
     r"\href{https://example.invalid/([18, p. 398])}{x}", "href 第一参"),
    (r"\textsuperscript{([18, p. 398])}",
     r"\textsuperscript{([18, p. 398])}", "textsuperscript 参数"),
    ("https://example.invalid/([18, p. 398])",
     "https://example.invalid/([18, p. 398])", "裸 URL 参数"),
    ("1. ([18, p. 398]) 条目", "1. ([18, p. 398]) 条目", "行首编号列表里的圆括号组"),
    (r"\parencite{K19} and ([18, p. 398]).",
     r"\parencite{K19} and \parencite[p.~398]{K18}.",
     "已有 cite 命令后正文引用仍照常转（反例不连坐）"),
]
for _src, _want, _label in _R1_PROTECTED:
    _o, _n, _ = cite(_src)
    check(f"R1 受保护 `{_label}`：输出逐字节 = 期望，n={'0' if _src == _want else '1'}",
          _o == _want, repr(_o))
for _src, _label in [("Text [18, p. 398].", "裸正向"),
                     ("Text ([18, p. 398]).", "组正向")]:
    _o, _n, _ = cite(_src)
    check(f"R1 {_label} 仍转换 1 处 → `\\parencite[p.~398]{{K18}}`",
          _o == "Text \\parencite[p.~398]{K18}." and _n == 1, (repr(_o), _n))
for _src, _label in [("Text [99, p. 398].", "越界"),
                     ("Text [999, p. 1].", "越界（3 位上界）"),
                     ("See ([999]).", "圆括号组内越界")]:
    _o, _n, _iss = cite(_src)
    check(f"R1 缺号/越界（{_label}）原样保留不猜键",
          _o == _src and _n == 0 and any("超出" in x for x in _iss), (_o, _iss))

# ══════════════════════════════════════════════════════════════════════════
print("── R1-c 返工：脚注内已有 cite 可选参不得二次转换（en/zh，失败即非零）──")
# ══════════════════════════════════════════════════════════════════════════
# R1 的 delegated_cn_group 窄例外原把 `(\parencite[2]{key})` / `(\cite[490]{key})`
# 的可选参 `[2]`/`[490]` 当裸编号误判为「可委托 CN 组」，绕过掩码做二次转换，
# 产出游离 \parencite + 嵌套键（语料 F193/F267/F311 × en/zh 共 6 行回归）。
# 下列断言钉住 preR1 正确行为：这些行必须 n=0、逐字节原样。
_R1C_FN_N2K = {2: "K2", 490: "K490", 18: "K18", 19: "K19", 33: "K33"}
for _lang, _src, _label in [
    ("en", r"x \footnote{y (\parencite[2]{priest2024}) z}",
     "en 脚注内已有 \\parencite[2]{key}"),
    ("en", r"x \footnote{y (\cite[490]{vaneemeren2014handbook}) z}",
     "en 脚注内已有 \\cite[490]{key}"),
    ("zh", r"x \footnote{y （\parencite[2]{priest2024}） z}",
     "zh 脚注内已有 \\parencite[2]{key}"),
    ("zh", r"x \footnote{y （\citep[2]{two}） z}",
     "zh 脚注内已有 \\citep[2]{key}"),
]:
    _o, _n, _ = cite(_src, lang=_lang, n2k=_R1C_FN_N2K)
    check(f"R1-c {_label} 不二次转换（n=0、逐字节 = preR1）",
          _o == _src and _n == 0, (repr(_o), _n))
# 反连坐：窄例外本意必须保住——脚注内 CN 式裸编号 + 外部定位仍照旧转换，
# 否则 b73_footnote_bare_cite 回归（守卫不可简单整段删除）。
for _lang, _src, _want, _label in [
    ("zh", r"见\footnote{详见（[18]，第33页）。}",
     r"见\footnote{详见（\parencite{K18}，第33页）。}", "zh 脚注内 CN 式（[N]，第M页）"),
    ("zh", r"见\footnote{（[18,19]，第33页）}",
     r"见\footnote{（\parencite{K18,K19}，第33页）}", "zh 脚注内多编号 CN 组"),
]:
    _o, _n, _ = cite(_src, lang=_lang, n2k=_R1C_FN_N2K)
    check(f"R1-c 保住窄例外：{_label} 仍转换（n=1、逐字节不变）",
          _o == _want and _n == 1, (repr(_o), _n))

# P2⑤/P2④/P2② 旧「已知未修」NOTE → 真断言（已修，失败即非零）
_o_mixzh, _n_mixzh, _ = cite("(see [18, p. 398] and [19], p. 5)", lang="zh")
check("P2④（已修）中文混合形态外层括号/空格原样保留（n=0，不半转吞空格）",
      _o_mixzh == "(see [18, p. 398] and [19], p. 5)" and _n_mixzh == 0, repr(_o_mixzh))
_o_sepen, _n_sepen, _ = cite("([18, p. 398]; [19, p. 55])", lang="en")
check("P2②（已修）英文多段用半角 `; `（不出现全角「；」）",
      _o_sepen == "(\\parencite[p.~398]{K18}; \\parencite[p.~55]{K19})"
      and _n_sepen == 1 and "；" not in _o_sepen, repr(_o_sepen))

# ══════════════════════════════════════════════════════════════════════════
print("── P2 ⑥ 全局默认不变：`--number-cite-map` 仍 False、产物不使用 `\\nocite` ──")
# ══════════════════════════════════════════════════════════════════════════

check("P2⑥ `build_docx(number_cite_map=…)` 默认仍是 False（未改全局默认）",
      inspect.signature(build.build_docx).parameters["number_cite_map"].default is False,
      inspect.signature(build.build_docx).parameters["number_cite_map"].default)
_tex6 = open(build.build_docx(os.path.join(TMP, "keymap.docx"), TMP, basename="keymap2",
                             lang="en", compile_requested=False, verbose=False).tex_path,
             encoding="utf-8").read()
check("P2⑥ 交付的英文 .tex 不含 `\\nocite`（不强制打印全部条目）",
      "\\nocite" not in _tex6, "")

# ══════════════════════════════════════════════════════════════════════════
print("── P2 ⑦ `.tex` 里每个新 cite 键都能在交付的 reference.bib 找到 ──")
# ══════════════════════════════════════════════════════════════════════════

_cites = re.findall(r"\\parencite(?:\[[^\]]*\])?\{([^}]*)\}", _texm)
_keys_used = {k.strip() for grp in _cites for k in grp.split(",") if k.strip()}
_defined = set(re.findall(r"@\w+\{([^,]+),", _bibm))
check("P2⑦ 新 cite 用了 2 个键（18、19 各一处）", len(_keys_used) == 2, _keys_used)
check("P2⑦ 每个新 cite 键都在交付的 reference.bib 里有定义",
      _keys_used <= _defined, (_keys_used - _defined, sorted(_defined)[:4]))
check("P2⑦ 交付的 bib 里被引条目带着源稿正文（M18/M19 都在，不是空壳）",
      "M18" in _bibm and "M19" in _bibm, "")
check("P2⑦ `([18, p. 398])` 的原始纯文本在 .tex 里彻底消失（不会编译成硬写的方括号）",
      "([18, p." not in _texm and "[18, p. 398]" not in _texm, "")

# ══════════════════════════════════════════════════════════════════════════
shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
