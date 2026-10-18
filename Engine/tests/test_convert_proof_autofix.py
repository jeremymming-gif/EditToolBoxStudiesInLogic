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
from slcn.build import _PROOF_AUTOFIX_IDS, _autofix_proof  # noqa: E402
from slcn.docxxml import W_NS  # noqa: E402

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

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
