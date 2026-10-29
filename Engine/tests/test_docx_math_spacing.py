#!/usr/bin/env python3.14
r"""OMML 公式转换：**Unicode 数学命令与后续字母的粘连**（第 141 条，2026-10-18）。

缺陷
----
`slcn/docxxml.py::_math_to_tex()` 想把 `∃`→`\exists`、`¬`→`\lnot` 之后与后面的字母
之间补一个空格（防 `\landA` 粘连），判据写成了 `cmd.isalpha()`。但 `cmd` 以反斜杠开头，
`"\exists".isalpha()` **恒为 False** → 永远不补空格 → `∃q` 变 `\existsq`、`¬q` 变
`\lnotq`，xelatex 报 Undefined control sequence，整篇编译失败。

实测：期 87 子案例 5（06 Xiaomei_Tang）正文 135 处 OMML 公式，产物里出现
`$\existsq\in K_{n-1}$` / `$...⊩\lnotq$` → 转换 rc=2、PDF 未通过。

本套件钉住：
  ① 命令与后续字母之间**必须有空格**（`∃q` / `¬q` / `∀x`）；
  ② `\sqrt{}` 这类以 `}` 结尾的命令不补空格（`√x` → `\sqrt{}x`）；
  ③ 旧缺陷的负例不出现（`\existsq` / `\lnotq` 不得出现）；
  ④ 强制符号 `⊩` 映射到 `\Vdash`（此前原样留在数学模式里 → `Missing character`）；
  ⑤ OMML 节点端到端（合成 `<m:oMath>`：∃、q 分属两个 `<m:r>`）；
  ⑥ `ole_object_stats()`：MathType/OLE 对象计数（w:object 与 word/embeddings）。

只写内存/临时目录；不跑 xelatex、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_docx_math_spacing.py
"""
import os
import sys
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import rules  # noqa: E402
from slcn.docxxml import (count_omml, _math_to_tex, _run_segments, ole_object_stats,  # noqa: E402
                         omml_to_latex, symbol_run_stats, W_NS)

FAILS = []
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══════════════════════════════════════════════════════════════════════════
# ① 命令后必须补空格（旧实现恒不补）
# ══════════════════════════════════════════════════════════════════════════
check("① ∃q → `\\exists q`（不是 `\\existsq`）",
      _math_to_tex("∃q") == r"\exists q", repr(_math_to_tex("∃q")))
check("① ¬q → `\\lnot q`（不是 `\\lnotq`）",
      _math_to_tex("¬q") == r"\lnot q", repr(_math_to_tex("¬q")))
check("① ∀x → `\\forall x`", _math_to_tex("∀x") == r"\forall x",
      repr(_math_to_tex("∀x")))
check("① 连续符号：∃q∈K → `\\exists q\\in K`（反斜杠本身即命令边界）",
      _math_to_tex("∃q∈K") == r"\exists q\in K", repr(_math_to_tex("∃q∈K")))
check("③ 旧缺陷负例不出现（不得有 \\existsq / \\lnotq）",
      "\\existsq" not in _math_to_tex("∃q") and "\\lnotq" not in _math_to_tex("¬q"))

# ══════════════════════════════════════════════════════════════════════════
# ② 以 } 结尾的命令不补空格
# ══════════════════════════════════════════════════════════════════════════
check("② √x → `\\sqrt{}x`（`}` 结尾命令自带定界，不补空格）",
      _math_to_tex("√x") == r"\sqrt{}x", repr(_math_to_tex("√x")))

# ══════════════════════════════════════════════════════════════════════════
# ④ 强制符号 ⊩ → \Vdash（模板已加载 amssymb）
# ══════════════════════════════════════════════════════════════════════════
check("④ ⊩ 映射到 \\Vdash",
      _math_to_tex("⊩") == r"\Vdash", repr(_math_to_tex("⊩")))
check("④ ⊩ 与后续字母之间补空格（⊩q → `\\Vdash q`）",
      _math_to_tex("⊩q") == r"\Vdash q", repr(_math_to_tex("⊩q")))
check("④ 对偶符号映射（⊬→\\nvdash、⊭→\\nvDash）",
      rules.UNICODE_MATH.get("⊬") == r"\nvdash" and rules.UNICODE_MATH.get("⊭") == r"\nvDash")
check("④ 正文里的 ⊩ 也包成数学模式（normalize_unicode_math）",
      rules.normalize_unicode_math("A⊩B")[0] == r"A$\Vdash$B",
      rules.normalize_unicode_math("A⊩B")[0])
check("④ ⊨ 仍映射 \\models（旧映射不回退）",
      _math_to_tex("⊨") == r"\models", repr(_math_to_tex("⊨")))
check("④ ≃ 映射 \\simeq（子案例 3：正文 `$A$≃$B$` 不再 Missing character）",
      rules.UNICODE_MATH.get("≃") == r"\simeq"
      and rules.normalize_unicode_math("A≃B")[0] == r"A$\simeq$B",
      rules.normalize_unicode_math("A≃B")[0])
check("④ ∼ 映射 \\sim、≅ 映射 \\cong",
      rules.UNICODE_MATH.get("∼") == r"\sim"
      and rules.UNICODE_MATH.get("≅") == r"\cong")

# ══════════════════════════════════════════════════════════════════════════
# ⑤ OMML 端到端：∃ / q 分属两个 <m:r>
# ══════════════════════════════════════════════════════════════════════════
_OMML = (
    f'<m:oMath xmlns:m="{M_NS}">'
    "<m:r><m:t>∃</m:t></m:r><m:r><m:t>q</m:t></m:r>"
    "<m:r><m:t>∈</m:t></m:r><m:r><m:t>K</m:t></m:r>"
    "</m:oMath>"
)
_el = ET.fromstring(_OMML)
_tex = _math_to_tex(omml_to_latex(_el))
check("⑤ 合成 oMath（∃ + q + ∈ + K）→ `\\exists q\\in K`",
      _tex == r"\exists q\in K", repr(_tex))
check("⑤ 端到端不粘命令（无 \\existsq）", "\\existsq" not in _tex)

# ══════════════════════════════════════════════════════════════════════════
# ⑥ MathType/OLE 对象计数（静默丢公式的告警依据）
# ══════════════════════════════════════════════════════════════════════════
_xml = "<w:document><w:object/><w:object/><w:drawing/></w:document>"
_names = ["word/document.xml", "word/embeddings/oleObject1.bin",
          "word/embeddings/oleObject2.bin", "word/embeddings/"]
check("⑥ ole_object_stats：w:object 计数 = 2、embeddings 文件 = 2（目录项不算）",
      ole_object_stats(_xml, _names) == (2, 2), str(ole_object_stats(_xml, _names)))
check("⑥ ole_object_stats：普通 docx（无 OLE）返回 (0, 0)",
      ole_object_stats("<w:document/>", ["word/document.xml"]) == (0, 0))

# ══════════════════════════════════════════════════════════════════════════
# ⑦ 符号字体字符（<w:sym>）计数（子案例 3 的 39 处 LogicA 符号）
# ══════════════════════════════════════════════════════════════════════════
_sym_xml = (
    '<w:document><w:r><w:sym w:font="LogicA" w:char="0022"/></w:r>'
    '<w:r><w:sym w:font="LogicA" w:char="005e"/></w:r>'
    '<w:r><w:sym w:font="Wingdings" w:char="0040"/></w:r></w:document>'
)
check("⑦ LogicA 已知符号已映射进正文 → 不再计为静默丢失（只余 Wingdings 1）",
      symbol_run_stats(_sym_xml) == {"Wingdings": 1},
      str(symbol_run_stats(_sym_xml)))
check("⑦ LogicA 未列入映射的序号仍计数（防漏报）",
      symbol_run_stats('<w:document><w:sym w:font="LogicA" w:char="00FF"/></w:document>')
      == {"LogicA": 1},
      str(symbol_run_stats('<w:document><w:sym w:font="LogicA" w:char="00FF"/></w:document>')))
check("⑦ symbol_run_stats：无 <w:sym> 返回空 dict",
      symbol_run_stats("<w:document><w:r><w:t>文字</w:t></w:r></w:document>") == {})

# ══════════════════════════════════════════════════════════════════════════
# ⑧ <w:sym> → 正文 Unicode（LogicA 私有序号映射；子案例 3 的 39 处符号）
# ══════════════════════════════════════════════════════════════════════════
_logica_cases = {"0022": "→", "0040": "=", "004E": "∼", "0052": "∈",
                 "005D": "∅", "005E": "∞"}
for _code, _ch in _logica_cases.items():
    _run = ET.fromstring(
        f'<w:r xmlns:w="{W_NS}"><w:sym w:font="LogicA" w:char="{_code}"/></w:r>')
    _segs = _run_segments(_run, set())
    check(f"⑧ LogicA {_code} → {_ch!r}",
          len(_segs) == 1 and _segs[0].kind == "text" and _segs[0].text == _ch,
          repr([(x.kind, x.text) for x in _segs]))

# ⑨ oMath 计数：带属性也算、oMathPara 不计（子案例 3 的 74 vs 1）
_math_xml = ('<w:document><m:oMath><m:r/></m:oMath>'
             '<m:oMath xmlns:m="x" foo="1"><m:r/></m:oMath>'
             '<m:oMathPara><m:oMath/></m:oMathPara></w:document>')
check("⑨ count_omml：带属性 oMath 计入、oMathPara 本身不计（=3，含其中嵌套 oMath）",
      count_omml(_math_xml) == 3, str(count_omml(_math_xml)))

# ══════════════════════════════════════════════════════════════════════════
# ⑩ 公式里的中文/全角标点 → \text{}；LogicA F072/F073 → ⟨⟩（期90/05 Xiaoyu_Lin）
# ══════════════════════════════════════════════════════════════════════════
from slcn.docxxml import _math_text_latex  # noqa: E402
check("⑩ 公式中文包 \text{}、箭头仍留给数学命令",
      _math_text_latex("（x是具有“p→p”形式的命题）")
      == "\\text{（}x\\text{是具有“}p→p\\text{”形式的命题）}",
      repr(_math_text_latex("（x是具有“p→p”形式的命题）")))
_sym2 = '<w:document><w:sym w:font="LogicA" w:char="F072"/><w:sym w:font="LogicA" w:char="F073"/></w:document>'
check("⑩ LogicA F072/F073 已映射，不再计缺失",
      symbol_run_stats(_sym2) == {}, str(symbol_run_stats(_sym2)))
_run2 = ET.fromstring(
    f'<w:r xmlns:w="{W_NS}"><w:sym w:font="LogicA" w:char="F072"/>'
    f'<w:t>雪是白的</w:t><w:sym w:font="LogicA" w:char="F073"/></w:r>')
_segs2 = _run_segments(_run2, set())
check("⑩ F072/F073 → ⟨/⟩ 并保留中间文字",
      "".join(x.text for x in _segs2 if x.kind == "text") == "⟨雪是白的⟩",
      repr([(x.kind, x.text) for x in _segs2]))

# ══════════════════════════════════════════════════════════════════════════
# ⑪ 公式内「仍会漏」的字符：全角块/〇/U+3000 → \text{}；ASCII 特殊字符 → 转义
#    （期 90/07 Jiayi_He 的 `＝` U+FF1D 实测 2 处 Missing character；
#      期 87 报告 §C 的 C-1/C-2；E2 task-13）
# ══════════════════════════════════════════════════════════════════════════
check("⑩ 回归锚点：原夹具输出逐字节不变（C-4 死代码清理不改变行为）",
      _math_text_latex("（x是具有“p→p”形式的命题）")
      == "\\text{（}x\\text{是具有“}p→p\\text{”形式的命题）}",
      repr(_math_text_latex("（x是具有“p→p”形式的命题）")))

check("⑪ 全角块 U+FF00–FFEF（＝）→ 包进 \\text{}（不再留在数学模式 → 期90/07 Missing char 归零）",
      _math_text_latex("a＝b") == "a\\text{＝}b", repr(_math_text_latex("a＝b")))
check("⑪ 全角数字连段（第２章）→ 整段一个 \\text{}",
      _math_text_latex("第２章") == "\\text{第２章}", repr(_math_text_latex("第２章")))
check("⑪ 〇(U+3007) → 包进 \\text{}（CJK 符号区不在 4E00-9FFF 里）",
      _math_text_latex("〇是零") == "\\text{〇是零}", repr(_math_text_latex("〇是零")))
check("⑪ U+3000 全角空格 → \\hspace{1em}（实测在 \\text{} 里会 Missing char）",
      _math_text_latex("甲\u3000乙") == "\\text{甲\\hspace{1em}乙}",
      repr(_math_text_latex("甲\u3000乙")))
check("⑪ ASCII `%` 必须转义（否则整行变注释、编译坏）",
      _math_text_latex("占100%") == "\\text{占}100\\text{\\%}",
      repr(_math_text_latex("占100%")))
check("⑪ ASCII `&` `#` `$` 同样转义（对齐符/宏参数/数学开关）",
      _math_text_latex("a&b#c$d") == "a\\text{\\&}b\\text{\\#}c\\text{\\$}d",
      repr(_math_text_latex("a&b#c$d")))
check("⑪ 反例：`_ ^` 保持数学语义（不包 \\text{}，`$x_1$` 不是文字）",
      _math_text_latex("变量_a") == "\\text{变量}_a",
      repr(_math_text_latex("变量_a")))
check("⑪ 反例：`\\` 仍走数学分支；`{`/`}` 改为转义（第四十四批①补：OMML 文本里的"
      "花括号是字面字符，原样输出会 `Extra }`/`Missing $`，repo-73-05 实测）",
      _math_text_latex("中文\\指令") == "\\text{中文}\\\\text{指令}"
      and _math_text_latex("集合{x}") == "\\text{集合}\\{x\\}",
      repr((_math_text_latex("中文\\指令"), _math_text_latex("集合{x}"))))
check("⑪ 反例：数学符号映射不受影响（→/∈ 仍留给 _math_to_tex）",
      _math_text_latex("p→p∈A") == "p→p∈A", repr(_math_text_latex("p→p∈A")))
check("⑪ 确定性：同一输入重复调用输出逐字节相同（纯函数；且 `%` 只转义一次，不双转义）",
      _math_text_latex("占100%") == _math_text_latex("占100%")
      and _math_text_latex("占100%").count("\\%") == 1
      and "\\\\%" not in _math_text_latex("占100%"),
      repr(_math_text_latex("占100%")))

# ══════════════════════════════════════════════════════════════════════════
# ⑫ 第四十五批 A：正文 `<w:t>` 里的手打数学符号 / 不可渲染字符
#    88-02 Ning_Jiang：◻(U+25FB) 字体无字形 → 编译 Missing character；人工成品写 $\Box$。
#    同段夹带变体选择符 U+E0176；88-07 Bo_Liu：私用区 U+F0A7（无字体、人工成品无）。
# ══════════════════════════════════════════════════════════════════════════
from slcn.docxxml import _split_text_run  # noqa: E402

_segs12 = _split_text_run("必然算子◻P 与变体" + chr(0xE0176) + "选择符以及" + chr(0xF0A7) + "私用字符", False, False)
_kinds12 = [(sg.kind, sg.text) for sg in _segs12]
check("⑫ ◻ 转数学 `\\Box`（不再是字体里没有的原始字符）",
      ("math", r"\Box") in _kinds12, repr(_kinds12))
check("⑫ 变体选择符 U+E0176 / 私用区 U+F0A7 被丢弃（不可见/无字体，人工成品也无）",
      all(chr(0xE0176) not in t and chr(0xF0A7) not in t for _k, t in _kinds12), repr(_kinds12))
check("⑫ 普通文字逐字节保留（只拆特殊字符）",
      "".join(t for k, t in _kinds12 if k == "text") == "必然算子P 与变体选择符以及私用字符",
      repr("".join(t for k, t in _kinds12 if k == "text")))
_segs12b = _split_text_run("普通文本", True, True)
check("⑫ 无特殊字符：仍是一个 text Seg（与从前逐字节相同）",
      [(sg.kind, sg.text, sg.italic, sg.bold) for sg in _segs12b] == [("text", "普通文本", True, True)],
      repr(_segs12b))

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
