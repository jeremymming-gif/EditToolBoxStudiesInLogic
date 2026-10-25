#!/usr/bin/env python3.14
"""P1-6：一校「可自动改」子集接进初排（期 87 P1-6）的专项测试。

缺陷
-----
一校核查（`slcn/proofcheck.py`）把「顿号后紧跟引号」（ZH-06）与「中文之间的半角
逗号/句号/冒号/分号/问叹号」（ZH-01..ZH-05）标成 `fixable`，但只有人工跑
`proofcheck.py --fix` 才会改。初排转换（`slcn/build.py` 的 `build_docx()`）此前只跑
`rules.apply_rules(safe_only=True)`，两套规则表没有交集 → 生成稿里仍残留这些命中。
期 87 实测残留 20 处（ZH-06×19 + ZH-01×1）、全库初排稿（Word 通道）残留 123 处/29 篇，
而人工成品这些位置为 0（笔记仓 `期87-子案例核查-2026-10-18.md` §P1-6、案例 142）。

修法
----
`build_docx(proof_autofix=True)`：⑤ 预校正之后、写文件之前，用 `proofcheck` 的
**稳定两参数 API**（`check()` → 过滤 `Report` → `apply_fixes(tex, sub)`）只套用
**窄集 ZH-01..ZH-06**。ZH-08/09/14、EN-01..03 本批不默认改（无消融与人工对照）。
`proof_autofix=False` = 与接入前逐字节相同；异常只写 warning、不阻断出稿。

跑法
----
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_convert_proof_autofix.py
"""
import os
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, proofcheck  # noqa: E402
from slcn.build import (_PROOF_AUTOFIX_IDS, _autofix_proof, _bib_zero_cites_hint,  # noqa: E402
                          _count_cite_commands, _dash_hints, _report_bib_zero_cites)
from slcn import paths as _paths  # noqa: E402
from slcn.docxxml import W_NS  # noqa: E402

_manuscripts = _paths.manuscripts_root()

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# 合成最小 docx：`word/document.xml` 是唯一必需文件（parse_docx/read_blocks 只读它；
# word/footnotes.xml 缺失有 KeyError 兜底、不依赖 styles.xml/_rels）。
def make_docx(paras):
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    doc = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
    return path


# ══════════════════════════════════════════════════════════════════════════
# ①–④ 单元：_autofix_proof 只做窄集、语义与人工成品一致、幂等、不碰屏蔽区
# ══════════════════════════════════════════════════════════════════════════
check("① 白名单常量恰为 ZH-01..ZH-06（不夹带 ZH-08/09/14、EN）",
      _PROOF_AUTOFIX_IDS == ("ZH-01", "ZH-02", "ZH-03", "ZH-04", "ZH-05", "ZH-06"),
      repr(_PROOF_AUTOFIX_IDS))

_new6, _h6 = _autofix_proof("甲、“乙”", "zh")
check("① ZH-06 顿号后引号 → 删顿号（人工成品同款：甲“乙”）", _new6 == "甲“乙”", repr(_new6))
check("① 计数 = {ZH-06: 1}", _h6 == {"ZH-06": 1}, repr(_h6))

_new1, _h1 = _autofix_proof("即,那", "zh")
check("② ZH-01 中文间半角逗号 → 全角（即，那）", _new1 == "即，那", repr(_new1))
check("② 计数 = {ZH-01: 1}", _h1 == {"ZH-01": 1}, repr(_h1))

_new2, _h2 = _autofix_proof(_new6, "zh")
check("③ 幂等：对结果再跑一遍 → 原串、计数空", _new2 == _new6 and _h2 == {}, repr((_new2, _h2)))

# 屏蔽区：数学 / 引文键 / 注释 / verbatim —— 一个字节都不许动
for name, tex in (
    ("数学 $a,b$", "$a,b$"),
    ("引文键 \\parencite{a,b}", "见\\parencite{a,b}"),
    ("注释 % 甲、乙", "% 甲、乙"),
    ("verbatim \\verb|甲、乙|", "\\verb|甲、乙|"),
    ("ZH-08 短杠（白名单外）", "马丁-洛夫"),
    ("ZH-09 页码范围（白名单外）", "第1-2页"),
    ("ZH-14 集合竖线（白名单外）", "\\{a|b\\}"),
):
    _out, _h = _autofix_proof(tex, "zh")
    check(f"④ 屏蔽区/白名单外不动：{name}", _out == tex and _h == {}, repr((_out, _h)))

# 英文稿（lang=en）窄集是 zh 规则，必须 0 改动（build_docx 侧另按 lang=="zh" 守卫）
_en, _he = _autofix_proof("He said, \"a,b\"", "en")
check("④ 英文稿（lang=en）零改动", _en == "He said, \"a,b\"" and _he == {}, repr((_en, _he)))

# ══════════════════════════════════════════════════════════════════════════
# ⑤⑥ 端到端：合成 docx → build_docx 默认自动改 / proof_autofix=False 原样
# ══════════════════════════════════════════════════════════════════════════
_TITLE = "公式与标点的测试稿"
_AUTHOR = "张三"
_BODY = [
    "摘要：本文用合成稿验证初排会自动套用一校可自动改子集。",
    "关键词：测试；标点；初排",
    "第一段：命题“甲”、“乙”的写法在人工成品里是“甲”“乙”。",
    "第二段：若气温升高,则冰会融化。",
]
_DOCX = make_docx([_TITLE, _AUTHOR] + _BODY)


def _build(proof_autofix: bool) -> tuple[str, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        res = build.build_docx(
            docx_path=_DOCX, out_dir=tmp, basename="Case", lang="zh",
            verbose=False, proof_autofix=proof_autofix,
        )
        tex = open(res.tex_path, encoding="utf-8").read()
        return tex, res


_tex_on, _res_on = _build(True)
_tex_off, _res_off = _build(False)

check("⑤ 默认（proof_autofix=True）产物里 ZH-06 已改：出现「“甲”“乙”」",
      "“甲”“乙”" in _tex_on, repr([l for l in _tex_on.splitlines() if "甲" in l][:2]))
check("⑤ 默认产物里 ZH-01 已改：出现「升高，则」", "升高，则" in _tex_on)
check("⑥ proof_autofix=False 产物保留原样（顿号/半角逗号仍在）",
      "“甲”、“乙”" in _tex_off and "升高,则" in _tex_off)
check("⑤ 默认产物里这 6 条规则残留 = 0（用 proofcheck 复核最终 tex）",
      not [h for h in proofcheck.check(_tex_on, lang="zh").hits
           if h.rule_id in _PROOF_AUTOFIX_IDS and h.fixable])
check("⑤ BuildResult.rule_hits 里合并记录了自动改（ZH-06/ZH-01 至少一条 >0）",
      any(_res_on.rule_hits.get(r, 0) > 0 for r in ("ZH-06", "ZH-01")),
      repr(_res_on.rule_hits))

# ⑤′ 失败不阻断：临时把 proofcheck.check 换成抛错版 → 产物仍生成 + warning 记录
_orig_check = proofcheck.check


def _boom(*a, **k):
    raise RuntimeError("模拟一校模块异常")


proofcheck.check = _boom
try:
    _tex_boom, _res_boom = _build(True)
    _ok_boom = "“甲”、“乙”" in _tex_boom  # 未自动改 = 保持原样，但仍出稿
finally:
    proofcheck.check = _orig_check
check("⑤′ 自动改异常时**不阻断出稿**（产物仍写出且完整、内容保持原样）",
      _ok_boom and len(_tex_boom) > 1000 and "\\begin{document}" in _tex_boom)
check("⑤′ 异常写进 warnings（不让编辑无感）",
      any("一校可自动改子集未执行" in w for w in _res_boom.warnings),
      repr(_res_boom.warnings[-2:]))

# 清理临时 docx
try:
    os.unlink(_DOCX)
except OSError:
    pass

# ══════════════════════════════════════════════════════════════════════════
# ⑧ ZH-08 连接号：初排日志「待人工判断」提示（第四十三批）
#    —— 只写日志、不改产物；引擎不做语义自动分类（人名短横 vs 地名一字线不可靠区分）
# ══════════════════════════════════════════════════════════════════════════
_DASH_NEEDLE = "ZH-08 连接号待人工判断"
_GUIDE_NEEDLE = "判断指引"
_GUIDE_TEXT = "人名 / 编号 / 日期 / 字母序号 → 短横 \\edash；地名 / 路线 / 区间等 → 一字线"


def _hint_lines(res) -> list[str]:
    return [ln for ln in res.log if _DASH_NEEDLE in ln]


# ⑧-1 单元：屏蔽区（数学/注释）不算；命中数与 proofcheck 同口径
_tex8 = "正文 马丁-洛夫 与 $甲-乙$ 与 % 甲-乙\n北京-上海 区间"
_hints8 = _dash_hints(_tex8, "zh")
_pc8 = sum(1 for h in proofcheck.check(_tex8, lang="zh").hits if h.rule_id == "ZH-08")
check("⑧ 单元：_dash_hints 命中数 = proofcheck ZH-08 数（同一判据）",
      len(_hints8) == _pc8 == 2, f"{len(_hints8)} vs {_pc8}")
check("⑧ 单元：数学 $甲-乙$ / 注释 % 甲-乙 不进提示（屏蔽层生效）",
      all("甲-乙" not in ctx or "马丁-洛夫" in ctx or "北京-上海" in ctx
          for _line, ctx in _hints8),
      repr([c for _l, c in _hints8]))

# ⑧-2/⑧-3 端到端 Word 通道：命中→日志含指引+上下文；开关关→无提示；产物逐字节不变
def _sha_file(path: str) -> str:
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


_DOCX8 = make_docx([
    "连接号待判断的测试稿", "张三",
    "摘要：测试 ZH-08 提示。", "关键词：连接号",
    "第一段：关于让-雅克·卢梭的讨论，以及北京-上海的路线。",
    "第二段：该方案在假设-演绎法框架下成立。",
])


def _build8(flag: bool):
    with tempfile.TemporaryDirectory() as tmp:
        res = build.build_docx(docx_path=_DOCX8, out_dir=tmp, basename="Dash", lang="zh",
                               verbose=False, dash_hints=flag)
        return open(res.tex_path, encoding="utf-8").read(), res, _sha_file(res.tex_path)


_tex8on, _res8on, _sha8on = _build8(True)
_tex8off, _res8off, _sha8off = _build8(False)
_on_lines = _hint_lines(_res8on)
check("⑧ 合成稿：日志出现「ZH-08 连接号待人工判断：N 处」", bool(_on_lines), repr(_res8on.log[-8:]))
if _on_lines:
    _n_log = int(__import__("re").search(r"(\d+) 处", _on_lines[0]).group(1))
    _n_pc = sum(1 for h in proofcheck.check(_tex8on, lang="zh").hits if h.rule_id == "ZH-08")
    check("⑧ 合成稿：日志 N = proofcheck ZH-08 数", _n_log == _n_pc > 0, f"{_n_log} vs {_n_pc}")
else:
    _n_pc = 0
check("⑧ 合成稿：日志含判断指引（edash/一字线口径）",
      any(_GUIDE_NEEDLE in ln for ln in _res8on.log)
      and any("\\edash" in ln and "一字线" in ln for ln in _res8on.log),
      repr([ln for ln in _res8on.log if _GUIDE_NEEDLE in ln]))
check("⑧ 合成稿：上下文样例含实际命中（让-雅克 / 北京-上海 / 假设-演绎 至少 1 处）",
      any(("让-雅克" in ln) or ("北京-上海" in ln) or ("假设-演绎" in ln) for ln in _res8on.log),
      repr([ln for ln in _res8on.log if ln.strip().startswith("· 第")][:3]))
check("⑧ 开关关（dash_hints=False）→ 无任何 ZH-08 提示行", _hint_lines(_res8off) == [],
      repr(_hint_lines(_res8off)))
check("⑧ 产物逐字节不变：dash_hints 开/关 tex sha256 相同",
      _sha8on == _sha8off, f"{_sha8on[:16]} vs {_sha8off[:16]}")

# ⑧-4 无命中 → 不打印（同一套日志里没有任何 ZH-08 提示）
_DOCX8N = make_docx(["无连接号的测试稿", "张三", "摘要：测试。", "关键词：无",
                     "正文没有任何需要判断的连接号。"])
with tempfile.TemporaryDirectory() as _tmp8n:
    _res8n = build.build_docx(docx_path=_DOCX8N, out_dir=_tmp8n, basename="NoDash",
                              lang="zh", verbose=False)
check("⑧ 无命中：日志不打印 ZH-08 提示", _hint_lines(_res8n) == [], repr(_res8n.log[-5:]))
try:
    os.unlink(_DOCX8)
    os.unlink(_DOCX8N)
except OSError:
    pass

# ⑧-5 LaTeX 通道同样适用（只写日志、产物不变）
_tex_src8 = os.path.join(tempfile.mkdtemp(), "body8.tex")
open(_tex_src8, "w", encoding="utf-8").write(
    "\\documentclass{ctexart}\n\\begin{document}\n"
    "关于让-雅克·卢梭与北京-上海的讨论。\n\\end{document}\n")
try:
    with tempfile.TemporaryDirectory() as _t8a, tempfile.TemporaryDirectory() as _t8b:
        _ra = build.build_latex(_tex_src8, _t8a, basename="L", lang="zh",
                                verbose=False, dash_hints=True)
        _rb = build.build_latex(_tex_src8, _t8b, basename="L", lang="zh",
                                verbose=False, dash_hints=False)
        _sha_a = _sha_file(_ra.tex_path)
        _sha_b = _sha_file(_rb.tex_path)
        _la_hint = _hint_lines(_ra)
        _lb_hint = _hint_lines(_rb)
    check("⑧ LaTeX 通道：日志同样输出 ZH-08 提示", bool(_la_hint), repr(_la_hint[:1]))
    check("⑧ LaTeX 通道：开关关→无提示", _lb_hint == [])
    check("⑧ LaTeX 通道：产物逐字节不变（开/关 sha 相同）", _sha_a == _sha_b)
finally:
    try:
        os.unlink(_tex_src8)
    except OSError:
        pass

# ⑧-6 真实稿 pin：日志命中数 = proofcheck ZH-08 数（期 87 Word 稿；无稿件库时 SKIP）
if _manuscripts is not None:
    _real_dir = os.path.join(_manuscripts, "2026年第2期(87)", "06 Ruixue_Tang")
    if os.path.isdir(_real_dir):
        _ss8 = __import__("slcn.sources", fromlist=["identify"]).identify(_real_dir, verbose=False)
        if _ss8.docx:
            with tempfile.TemporaryDirectory() as _t8:
                _r8 = build.build_docx(_ss8.docx, _t8, basename="Real", lang="zh",
                                       verbose=False)
                _t8tex = open(_r8.tex_path, encoding="utf-8").read()
            _rl = _hint_lines(_r8)
            _rn = int(__import__("re").search(r"(\d+) 处", _rl[0]).group(1)) if _rl else 0
            _rpc = sum(1 for h in proofcheck.check(_t8tex, lang="zh").hits if h.rule_id == "ZH-08")
            check("⑧ 真实稿（期87 Ruixue_Tang）：日志命中数 = proofcheck ZH-08 数",
                  _rn == _rpc > 0, f"{_rn} vs {_rpc}")
        else:
            print("SKIP  ⑧ 真实稿 pin（该篇不是 Word 稿）")
    else:
        print("SKIP  ⑧ 真实稿 pin（本机没有该稿）")
else:
    print("SKIP  ⑧ 真实稿 pin（本机没有稿件库）")


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 第四十四批④：B 类可发现性提示（bib>0 且正文 0 引用；纯日志、产物不变）
#    背景：期 90 P0-B（案例 145）——源稿「正文编号引用 ↔ 无编号清单」体例，
#    biblatex 只打印被引条目 → PDF 参考文献区空的，但初排阶段编辑看不到。
#    判据刻意不猜「编号 ↔ 条目」映射；90-06 是脚注引注路径（有 30 处引用）→ 不误报。
# ══════════════════════════════════════════════════════════════════════════
_B_NEEDLE = "[B 类提示]"


def _b_hint_lines(res) -> list[str]:
    return [ln for ln in res.log if _B_NEEDLE in ln]


check("⑨ 单元：常见引用命令都计数（\\parencite/\\cite/\\footcite/\\nocite）",
      _count_cite_commands(
          "见\\parencite{a}、\\cite{b}、\\footcite{c}、\\textcite{d}、\\nocite{*}") == 5,
      str(_count_cite_commands("见\\parencite{a}、\\cite{b}、\\footcite{c}、\\textcite{d}、\\nocite{*}")))
check("⑨ 单元：注释里的引用不计数", _count_cite_commands("% 样例 \\parencite{a}\n正文") == 0)
check("⑨ 单元：数学区间里的反斜杠不计数（反例：数学区间不误报）",
      _count_cite_commands("$x=\\cal{cite}$ 与 \\[ \\foo{cite} \\]") == 0)
check("⑨ 单元：verbatim 里不计数",
      _count_cite_commands("\\begin{verbatim}\n\\cite{a}\n\\end{verbatim}") == 0)
check("⑨ 单元：preamble 里的定义不计数（只看 \\begin{document} 之后）",
      _count_cite_commands(
          "\\newcommand{\\x}{\\cite{a}}\n\\begin{document}\n正文无引用\n\\end{document}") == 0)

check("⑨ 判据：bib=0 → 不提示（空 bib 不误报）", _bib_zero_cites_hint("正文", 0) is None)
check("⑨ 判据：有引用 → 不提示", _bib_zero_cites_hint("见\\parencite{a}", 5) is None)
_b_hint = _bib_zero_cites_hint("正文没有引用命令", 2)
check("⑨ 判据：bib=2 且 0 引用 → 提示含条数 + PDF 参考文献区为空 + 人工补引用建议",
      _b_hint is not None and "2 条" in _b_hint[0]
      and "PDF 参考文献区会是空的" in _b_hint[0]
      and any("人工补" in ln for ln in _b_hint),
      repr(_b_hint))

_b_log: list[str] = []
_b_warn: list[str] = []
_rep_hit = _report_bib_zero_cites("正文", 3, _b_log.append, _b_warn, _b_log)
check("⑨ 提示写进初排日志**顶部**（log[0]）与 warnings 首位",
      _rep_hit and _b_log[0].startswith("⚠️ [B 类提示]") and _b_warn[0].startswith("⚠️ [B 类提示]"),
      repr(_b_log[:2]))
_b_log2: list[str] = []
_b_warn2: list[str] = []
check("⑨ 无命中时不写日志/warnings",
      _report_bib_zero_cites("见\\parencite{k}", 3, _b_log2.append, _b_warn2, _b_log2) is False
      and _b_log2 == [] and _b_warn2 == [])

# ⑨-1 LaTeX 通道：合成 body + 有 1 条 bib 但 0 引用 → 提示；开关关→无提示；产物 sha 相同
_B_TMP = tempfile.mkdtemp()
_B_TEX = os.path.join(_B_TMP, "body.tex")
open(_B_TEX, "w", encoding="utf-8").write(
    "\\documentclass{ctexart}\n\\title{测试稿}\n\\author{张三}\n"
    "\\begin{document}\n\\maketitle\n正文没有任何引用命令。\n\\end{document}\n")
_B_BIB = os.path.join(_B_TMP, "refs.bib")
open(_B_BIB, "w", encoding="utf-8").write(
    "@article{k1, title={T}, author={A}, year={2020}}\n")
try:
    with tempfile.TemporaryDirectory() as _bt1, tempfile.TemporaryDirectory() as _bt2:
        _bl_on = build.build_latex(_B_TEX, _bt1, bib_path=_B_BIB, basename="L", lang="zh",
                                   verbose=False, bib_zero_cites_hints=True)
        _bl_off = build.build_latex(_B_TEX, _bt2, bib_path=_B_BIB, basename="L", lang="zh",
                                    verbose=False, bib_zero_cites_hints=False)
        _bl_sha_on = _sha_file(_bl_on.tex_path)
        _bl_sha_off = _sha_file(_bl_off.tex_path)
    check("⑨ LaTeX 通道：bib>0 且 0 引用 → 日志顶部出现 B 类提示",
          bool(_b_hint_lines(_bl_on)) and _bl_on.log[0].startswith("⚠️ [B 类提示]"),
          repr(_bl_on.log[:2]))
    check("⑨ LaTeX 通道：开关关 → 无提示", _b_hint_lines(_bl_off) == [])
    check("⑨ LaTeX 通道：产物逐字节不变（提示开/关 sha256 相同）",
          _bl_sha_on == _bl_sha_off, f"{_bl_sha_on[:16]} vs {_bl_sha_off[:16]}")
finally:
    for _p in (_B_TEX, _B_BIB):
        try:
            os.unlink(_p)
        except OSError:
            pass
    try:
        os.rmdir(_B_TMP)
    except OSError:
        pass

def _issue_paper(issue_name: str, no: str) -> str:
    """按期目录名 + 篇序号发现来稿目录（不硬编码真实作者名；B-232）。"""
    if _manuscripts is None:
        return ""
    import glob as _glob
    hits = sorted(_glob.glob(os.path.join(_manuscripts, issue_name, no + " *")))
    return next((h for h in hits if os.path.isdir(h)), "")


# ⑨-2 真实稿 pin（期90/87 的 P0-B 案例；无稿件库时 SKIP）
if _manuscripts is not None:
    import slcn.sources as _sources

    # B-022 意图变化（批68）：90-04/90-08 的文末参考文献是 **Word 自动编号**，批68 物化
    # 清单序号后正文 `[n]` → `\parencite`，「bib>0 但 0 引用」的 B 类提示不应再出现；
    # 其余 3 例（90-06 脚注引注、87-5/87-7 无编号清单）口径不变。
    _REAL_B_CASES = (
        ("2026年第5期(90)", "04", False, "90-04（B-022 后已有 \\parencite）"),
        ("2026年第5期(90)", "06", False, "90-06 脚注引注路径（有 30 处引用）"),
        ("2026年第5期(90)", "08", False, "90-08（B-022 后已有 \\parencite）"),
        ("2026年第2期(87)", "06", True, "87-5"),
        ("2026年第2期(87)", "08", True, "87-7"),
    )
    for _iss, _no, _expect_b, _label in _REAL_B_CASES:
        _rd = _issue_paper(_iss, _no)
        if not os.path.isdir(_rd):
            print(f"SKIP  ⑨ 真实稿 {_label}（本机没有该稿）")
            continue
        _ssb = _sources.identify(_rd, verbose=False)
        if not _ssb.docx:
            print(f"SKIP  ⑨ 真实稿 {_label}（不是 Word 稿）")
            continue
        with tempfile.TemporaryDirectory() as _bt:
            _rb = build.build_docx(_ssb.docx, _bt, basename="Real", lang="zh", verbose=False)
        _got_b = bool(_b_hint_lines(_rb))
        check(f"⑨ 真实稿 {_label}：B 类提示{'出现' if _expect_b else '**不出现**（不误报）'}",
              _got_b == _expect_b,
              repr((_got_b, _rb.log[:2])))

    # ⑨-3 真实稿产物 sha 不变（90-04：提示开/关逐字节相同）
    _rd_b = _issue_paper("2026年第5期(90)", "04")
    if os.path.isdir(_rd_b):
        _ssb2 = _sources.identify(_rd_b, verbose=False)
        if _ssb2.docx:
            with tempfile.TemporaryDirectory() as _b1, tempfile.TemporaryDirectory() as _b2:
                _r_on = build.build_docx(_ssb2.docx, _b1, basename="R", lang="zh",
                                         verbose=False, bib_zero_cites_hints=True)
                _r_off = build.build_docx(_ssb2.docx, _b2, basename="R", lang="zh",
                                          verbose=False, bib_zero_cites_hints=False)
                _on_sha = _sha_file(_r_on.tex_path)
                _off_sha = _sha_file(_r_off.tex_path)
            # B-022 意图变化（批68）：90-04 已有 \parencite → B 类提示不再出现；
            # 「提示开关不影响产物」的硬判据（sha 相同）保留。
            check("⑨ 真实稿 90-04：产物逐字节不变（提示开/关 sha256 相同）+ B-022 后无 B 提示",
                  _on_sha == _off_sha and not _b_hint_lines(_r_on)
                  and _b_hint_lines(_r_off) == [],
                  f"{_on_sha[:16]} vs {_off_sha[:16]}")
else:
    print("SKIP  ⑨ 真实稿 pin（本机没有稿件库）")

# ══════════════════════════════════════════════════════════════════════════
# ⑩ B-221 C1：初排交接单（默认生成 / --no-handoff 关闭 / 重跑识别 / .tex 不变）
# ══════════════════════════════════════════════════════════════════════════
import argparse as _argparse  # noqa: E402
import re as _re  # noqa: E402

import convert as _convert  # noqa: E402
from slcn import handoff as _handoff  # noqa: E402

_ap_h = _argparse.ArgumentParser()
_convert._add_handoff_args(_ap_h)
check("⑩ --no-handoff：默认生成（handoff=True）", _ap_h.parse_args([]).handoff is True)
check("⑩ --no-handoff：显式关闭（handoff=False）",
      _ap_h.parse_args(["--no-handoff"]).handoff is False)

_h_docx = make_docx(["测试标题", "张三", "摘要：测试摘要。", "关键词：测试", "正文。"])
try:
    with tempfile.TemporaryDirectory() as _t_on:
        _r_h = build.build_docx(_h_docx, _t_on, basename="H", lang="zh", verbose=False)
        _hp = os.path.join(_t_on, "H" + _handoff.HANDOFF_SUFFIX)
        _htxt = open(_hp, encoding="utf-8").read() if os.path.isfile(_hp) else ""
        _h_tex_bytes = open(_r_h.tex_path, "rb").read()
        check("⑩ 默认生成 <stem>_初排交接单.txt（UTF-8/LF）",
              os.path.isfile(_hp) and "\r" not in _htxt, repr(_hp))
        _hlines = _htxt.split("\n")
        check("⑩ 四节固定、顺序正确",
              [ln for ln in _hlines if ln in _handoff.SECTION_TITLES]
              == list(_handoff.SECTION_TITLES))
        _hidx = [_hlines.index(t) for t in _handoff.SECTION_TITLES]
        check("⑩ 各节非空（有内容或「无」）",
              all(_hlines[a + 1:b] for a, b in
                  zip(_hidx, _hidx[1:] + [len(_hlines)])))
        check("⑩ BuildResult.handoff_path 指向交接单 + bibcheck 小字典槽位存在",
              _r_h.handoff_path == _hp
              and (_r_h.handoff_bib is None or isinstance(_r_h.handoff_bib, dict)))
        # Lead B-221 C1 硬要求：①不用 atomic_write（重跑不产生 .bak 污染产物目录）；
        # ②内容确定性（两目录/原地重跑逐字节同；无时间戳/绝对路径）。
        with tempfile.TemporaryDirectory() as _t_on2:
            build.build_docx(_h_docx, _t_on2, basename="H", lang="zh", verbose=False)
            _htxt2dir = open(os.path.join(_t_on2, "H" + _handoff.HANDOFF_SUFFIX),
                             encoding="utf-8").read()
        build.build_docx(_h_docx, _t_on, basename="H", lang="zh", verbose=False)
        _htxt_rerun = open(_hp, encoding="utf-8").read()
        _h_baks = [f for f in os.listdir(_t_on)
                   if f.startswith("H" + _handoff.HANDOFF_SUFFIX) and f.endswith(".bak")]
        check("⑩ 交接单确定性：两目录/原地重跑逐字节相同，且重跑不留 .bak（普通覆盖写）",
              _htxt2dir == _htxt == _htxt_rerun and _h_baks == [],
              f"baks={_h_baks}")
        check("⑩ 交接单不含绝对路径/时间戳（41 篇两遍比对口径）",
              _t_on not in _htxt and _t_on2 not in _htxt
              and _re.search(r"\d{8}-\d{6}-\d{6}", _htxt) is None)
    with tempfile.TemporaryDirectory() as _t_off:
        _r_off = build.build_docx(_h_docx, _t_off, basename="H", lang="zh",
                                  verbose=False, handoff=False)
        check("⑩ handoff=False：产物目录零新增交接单（旧行为）",
              not os.path.exists(os.path.join(_t_off, "H" + _handoff.HANDOFF_SUFFIX))
              and _r_off.handoff_path is None)
        check("⑩ handoff 开关不改 .tex（逐字节相同）",
              _h_tex_bytes == open(_r_off.tex_path, "rb").read())
    check("⑩ is_own_product 认交接单（第二次转换不被自己产物拦）",
          _convert.is_own_product("H" + _handoff.HANDOFF_SUFFIX, "H") is True)
    _hb = _handoff.render(
        stem="B", bib={"total": 2, "must": 1, "nice": 0, "unstructured": 0,
                       "report_path": "/tmp/B_参考文献核对.txt"},
        bibdb={"done": True, "safe": {"a": {}, "b": {}}, "candidates": {},
               "missed": [], "applied": [{"key": "a"}], "filled": 3},
        warnings=["短引经引用库安全命中（库内已核实，未编造）：「某引」→ key"])
    check("⑩ ① 汇总 bibcheck 结论 + 引用库回填条数 + 核对报告指针",
          "条目总数：2（必补 1 / 建议 0 / 未结构化 0）" in _hb
          and "回填 1 条 / 3 项" in _hb
          and "短引库→bib，生成期）：1 条" in _hb
          and "B_参考文献核对.txt" in _hb)
    _empty = _handoff.render(stem="Z")
    check("⑩ 交接单四节空时写「无」",
          all(t in _empty for t in _handoff.SECTION_TITLES)
          and _empty.count("无") >= 3)
finally:
    os.unlink(_h_docx)

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
