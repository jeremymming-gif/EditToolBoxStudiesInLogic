#!/usr/bin/env python3
r"""第六十三批 b：`--metadata-json` 结构化人工元数据（多作者 / 多单位 / CN-EN）。

背景（用户 2026-10-20 拍板）：取消 `.doc/.docx` 自动元数据识别，改为 GUI 人工
填写元数据；引擎新增 `--metadata-json <file>` 接收 GUI 生成的临时 JSON，值直接
写进交付产物。本套件覆盖（全部离线：不联网、不调付费 API、不跑 TeX；fixtures
全为虚构样例）：

  · `structured_fields()` 单元：单/多作者、多单位 `\\` 连接（首尾不留）、CN/EN
    两模板槽位分流、纯文本转义、页眉标题缺省、关键词口径、EN 模板 CN 槽位占位；
  · CLI 端到端：LaTeX 通道 zh/en 两模板、Word 通道、优先级（JSON > --title-cn >
    --metadata 文件）、空值不覆盖、空 JSON 不影响产物、缺字段占位、日志；
  · `load_metadata_json()` 单元 + CLI exit 2：不存在/语法错/顶层非对象/类型错/
    6 作者/中空作者/协同两段式；且**不产生产物**；
  · 默认零变化：无该参数时产物快照 + stdout 与 HEAD（`git archive HEAD`）逐字节
    相同；`structured=None` 与不传参数逐字节相同；JSON 只读不被改写、无临时残留。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch63b_metadata_json.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import convert as C  # noqa: E402
from slcn import build as B  # noqa: E402
from slcn import latex as _latex  # noqa: E402
from slcn import templates as T  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0
BREAK = "\\\\"          # LaTeX 换行（两个反斜杠）


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"SKIP  {name}  {why}")


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def write_json(path: str, obj) -> str:
    return write(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def snapshot(folder: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            out[os.path.relpath(p, folder)] = sha(p)
    return out


def make_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES",
                           "SLCN_MANUSCRIPTS")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return env


ENV = make_env()


def tpl_fields(lang: str) -> set[str]:
    """模板骨架里定义了哪些 `\\my*` 宏（与 build 的 tpl_fields 同源）。"""
    text = read(T.PRESETS[lang].skeleton)
    return set(_latex.parse_macros(text))


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def escape_xml(t: str) -> str:
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def make_docx(paras: list[str], path: str) -> str:
    """只含 word/document.xml 的最小 docx（与 test_tail_block.py 同法）。"""
    ps = "".join(f"<w:p><w:r><w:t>{escape_xml(t)}</w:t></w:r></w:p>" for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


def ensure_head_engine(tmp: str) -> str:
    """`git archive HEAD` → 改动前的引擎（默认零变化对照用）。"""
    dest = os.path.join(tmp, "head")
    try:
        tar = subprocess.run(["git", "-C", _REPO, "archive", "HEAD"],
                             capture_output=True, timeout=120)
        if tar.returncode != 0:
            return ""
        os.makedirs(dest, exist_ok=True)
        p = os.path.join(tmp, "head.tar")
        with open(p, "wb") as f:
            f.write(tar.stdout)
        with tarfile.open(p) as tf:
            tf.extractall(dest)
        eng = os.path.join(dest, "Engine")
        return eng if os.path.isfile(os.path.join(eng, "convert.py")) else ""
    except Exception:  # noqa: BLE001
        return ""


def run_convert(src: str, out: str, extra: list[str], *,
                engine: str = _ENGINE, name: str = "Meta") -> subprocess.CompletedProcess:
    """跑一次 CLI（`--name` 固定、`--no-original` 关掉原文拷贝，快照稳定）。"""
    cmd = [sys.executable, os.path.join(engine, "convert.py"),
           "--source", src, "--out", out, "--name", name, "--no-original"]
    cmd += extra
    return subprocess.run(cmd, cwd=_ENGINE, env=ENV, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def out_tex(out: str, name: str = "Meta") -> str:
    return os.path.join(out, name, f"{name}.tex")


def macros(tex_path: str, *names: str) -> dict[str, str]:
    """产物里的 `\\my*` 宏值；不指定 names 时返回全部（方便整套断言）。"""
    text = read(tex_path)
    if not names:
        return {n: m.value for n, m in _latex.parse_macros(text).items()}
    return {n: _latex.get_macro_value(text, n) for n in names}


# ── 虚构 fixtures（不来自任何真实稿件）────────────────────────────────────
_TEX_SRC = r"""\documentclass{article}
\begin{document}
这里是中文正文测试。
\end{document}
"""

_TEX_META = r"""\documentclass{article}
\newcommand{\mytitle}{元数据题名}
\newcommand{\myfirstauthor}{{\kaishu 王五}}
\newcommand{\myfirstaffiliation}{元数据单位}
\newcommand{\myfirstemail}{old@x.cn}
\newcommand{\myabstract}{元数据摘要}
\begin{document}
\end{document}
"""

# 双作者元数据（R4：JSON 作者数少于源稿槽位时「不清空、只 warning」）
_TEX_META_TWO = r"""\documentclass{article}
\newcommand{\mytitle}{双作者元数据题名}
\newcommand{\myfirstauthor}{{\kaishu 王五}}
\newcommand{\mysecondauthor}{{\kaishu 赵六}}
\newcommand{\mysecondaffiliation}{第二元数据单位}
\begin{document}
\end{document}
"""

_JSON_ONE = {
    "schema_version": 1,
    "title_cn": "测试题名", "title_en": "A Test Title",
    "abstract_cn": "这是中文摘要。", "abstract_en": "This is the abstract.",
    "keywords_cn": "测试；逻辑", "keywords_en": "test; logic",
    "authors": [{
        "name_cn": "张三", "name_en": "Zhang San",
        "affiliations_cn": ["中山大学哲学系", "逻辑与认知研究所"],
        "affiliations_en": ["Sun Yat-sen University", "Institute of Logic"],
        "email": "zs@example.edu.cn",
    }],
}

_JSON_THREE = {
    "schema_version": 1,
    "title_cn": "三位作者测试", "title_en": "Three Authors Test",
    "abstract_cn": "摘要甲。", "abstract_en": "Abstract A.",
    "keywords_cn": "甲；乙；丙",
    "authors": [
        {"name_cn": "甲一", "name_en": "Jia Yi",
         "affiliations_cn": ["甲大学", "甲研究院"], "affiliations_en": ["A Univ", "A Inst"],
         "email": "a@example.cn"},
        {"name_cn": "乙二", "name_en": "Yi Er",
         "affiliations_cn": ["乙大学", "乙研究院"], "affiliations_en": ["B Univ", "B Inst"],
         "email": "b@example.cn"},
        {"name_cn": "丙三", "name_en": "Bing San",
         "affiliations_cn": ["丙大学"], "affiliations_en": ["C Univ"],
         "email": "c@example.cn"},
    ],
}


# ── ① structured_fields 单元：映射 / 多单位 / 转义 / 关键词 / 占位清单 ──────


def test_unit_structured(tmp: str) -> None:
    zh = tpl_fields("zh")
    en = tpl_fields("en")
    f, pairs, warns, n = B.structured_fields(_JSON_ONE, False, zh)
    check("①-1 CN 模板：中英题名/摘要分流 + 关键词进 mykeywords",
          n == 1
          and f.get("mytitle") == "测试题名" and f.get("mytitleEN") == "A Test Title"
          and f.get("myabstract") == "这是中文摘要。"
          and f.get("myabstractEN") == "This is the abstract."
          and f.get("mykeywords") == "测试；逻辑", str(f))
    check("①-2 CN 模板：作者名带 \\kaishu、中英姓名分槽",
          f.get("myfirstauthor") == "\\kaishu 张三"
          and f.get("myfirstauthorEN") == "Zhang San", repr(f.get("myfirstauthor")))
    check("①-3 多单位：逐项写入并按模板 `\\\\` 连接（首尾不留）",
          f.get("myfirstaffiliation") == "中山大学哲学系" + BREAK + "逻辑与认知研究所"
          and f.get("myfirstaffiliationEN")
          == "Sun Yat-sen University" + BREAK + "Institute of Logic"
          and not str(f.get("myfirstaffiliation", "")).startswith("\\\\")
          and not str(f.get("myfirstaffiliation", "")).endswith("\\\\"),
          repr(f.get("myfirstaffiliation")))
    check("①-4 邮箱中英页共用（myfirstemail）+ 页眉标题缺省取主语言标题",
          f.get("myfirstemail") == "zs@example.edu.cn"
          and f.get("myrunningtitle") == "测试题名")
    check("①-5 全字段给齐时无补充占位符；英文关键词 warning（CN 模板无此槽）",
          pairs == () and any("中文模板无英文关键词字段" in w for w in warns), repr(warns))

    esc = {"title_cn": " 题  目\n空行 ", "authors": [{
        "name_cn": "甲&乙", "affiliations_cn": ["A&B 50%", "R&D_1"],
        "affiliations_en": ["X"], "email": "a_b@x.cn"}]}
    f2, _p2, _w2, _n2 = B.structured_fields(esc, False, zh)
    check("①-6 纯文本转义：& % _ 与空白折行（不把用户输入当 LaTeX）",
          f2.get("mytitle") == "题 目 空行"
          and f2.get("myfirstauthor", "").endswith("甲\\&乙")
          and f2.get("myfirstaffiliation")
          == "A\\&B 50\\%" + BREAK + "R\\&D\\_1"
          and f2.get("myfirstemail") == "a\\_b@x.cn", repr(f2))

    f3, pairs3, warns3, n3 = B.structured_fields(_JSON_ONE, True, en)
    check("①-7 EN 模板：mytitle=英文、mytitleCN=中文，作者 EN/CN 两套槽位",
          n3 == 1
          and f3.get("mytitle") == "A Test Title" and f3.get("mytitleCN") == "测试题名"
          and f3.get("myfirstauthor") == "Zhang San"
          and f3.get("myfirstauthorCN") == "张三"
          and f3.get("myfirstaffiliation")
          == "Sun Yat-sen University" + BREAK + "Institute of Logic"
          and f3.get("myfirstaffiliationCN")
          == "中山大学哲学系" + BREAK + "逻辑与认知研究所"
          and f3.get("myrunningtitle") == "A Test Title", repr(f3))
    check("①-8 EN 模板：关键词两语言都无槽位 → warning",
          any("英文模板无关键词字段" in w for w in warns3), repr(warns3))

    meta_missing = {"authors": [
        {"name_en": "Author A", "affiliations_en": ["Uni A"], "email": "a@x.cn"},
        {"name_en": "Author B"},
    ]}
    _f, pairs_en, _w, _n = B.structured_fields(meta_missing, True, en)
    pd = dict(pairs_en)
    check("①-9 EN 模板缺 CN 字段：给 my<N>authorCN/affiliationCN 占位补充清单",
          pd.get("myfirstauthorCN") == "第一作者中文姓名"
          and pd.get("myfirstaffiliationCN") == "第一作者中文单位"
          and pd.get("mysecondauthorCN") == "第二作者中文姓名"
          and pd.get("mysecondaffiliationCN") == "第二作者中文单位", repr(pd))
    _fz, pairs_zh, _wz, _nz = B.structured_fields(meta_missing, False, zh)
    pz = dict(pairs_zh)
    check("①-10 CN 模板：缺主语言姓名/单位/邮箱的槽位都进占位补充清单（构建侧去重）",
          pz.get("myfirstauthor") == "第一作者"
          and pz.get("mysecondauthor") == "第二作者"
          and pz.get("mysecondaffiliation") == "第二作者单位"
          and pz.get("mysecondemail") == "第二作者邮箱", repr(pz))
    f5, p5, w5, n5 = B.structured_fields({}, False, zh)
    check("①-11 空 dict：什么都不写（默认零变化的前提）",
          f5 == {} and p5 == () and w5 == [] and n5 == 0)

    six = {"authors": [{"name_cn": f"作者{i}"} for i in range(6)]}
    try:
        B.structured_fields(six, False, zh)
        err = ""
    except ValueError as e:
        err = str(e)
    check("①-12 直调 structured_fields：>5 作者抛 ValueError（不静默截断）",
          "最多 5 位作者" in err, err)

    api = os.path.join(tmp, "api")
    src = write(os.path.join(api, "src", "main.tex"), _TEX_SRC)
    try:
        B.build_latex(src, os.path.join(api, "l"), basename="X", lang="zh",
                      verbose=False, structured=six)
        err_l = ""
    except ValueError as e:
        err_l = str(e)
    check("①-13 直调 build_latex：>5 作者抛 ValueError", "最多 5 位作者" in err_l, err_l)
    docx_api = make_docx(["五作者测试", "王小明", "摘要：测试。", "正文。"],
                         os.path.join(api, "w", "paper.docx"))
    try:
        B.build_docx(docx_api, os.path.join(api, "d"), basename="Y", lang="zh",
                     verbose=False, proof_autofix=False, structured=six)
        err_d = ""
    except ValueError as e:
        err_d = str(e)
    check("①-14 直调 build_docx：>5 作者抛 ValueError", "最多 5 位作者" in err_d, err_d)


# ── ② CLI：LaTeX 通道 zh 模板（单作者全套 + 日志 + JSON 只读）──────────────


def test_cli_zh_one(tmp: str) -> None:
    d = os.path.join(tmp, "zh1")
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    js = write_json(os.path.join(d, "in", "meta.json"), _JSON_ONE)
    before = sha(js)
    out = os.path.join(d, "out")
    r = run_convert(src, out, ["--metadata-json", js])
    tex = read(out_tex(out)) if os.path.isfile(out_tex(out)) else ""
    m = macros(out_tex(out)) if tex else {}
    check("②-1 zh 单作者 CLI exit 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    check("②-2 日志口径：加载行「N 位作者、M 项输入字段」+ 应用行「写入 N 位作者 / X 个模板字段」",
          re.search(r"人工元数据（--metadata-json）：1 位作者、\d+ 项输入字段",
                    r.stdout) is not None
          and re.search(r"人工元数据：写入 1 位作者 / \d+ 个模板字段",
                        r.stdout) is not None, r.stdout[-700:])
    check("②-3 产物宏值：标题/摘要/关键词/作者/多单位单位/邮箱/页眉标题",
          m.get("mytitle") == "测试题名" and m.get("mytitleEN") == "A Test Title"
          and m.get("myabstract") == "这是中文摘要。"
          and m.get("mykeywords") == "测试；逻辑"
          and m.get("myfirstauthor") == "\\kaishu 张三"
          and m.get("myfirstauthorEN") == "Zhang San"
          and m.get("myfirstaffiliation")
          == "中山大学哲学系" + BREAK + "逻辑与认知研究所"
          and m.get("myfirstemail") == "zs@example.edu.cn"
          and m.get("myrunningtitle") == "测试题名", str(m))
    check("②-4 给齐的字段不再印【未识别】（标题页可见的正确性）",
          "【未识别" not in tex)
    check("②-5 JSON 文件只读：运行后 sha 不变", bool(tex) and sha(js) == before)
    check("②-6 中文模板英文关键词 warning（既有口径：只进 CN 页，英文关键词人工放置）",
          "中文模板无英文关键词字段" in r.stdout, r.stdout[-400:])


# ── ③ CLI：LaTeX 通道 zh 多作者 × 多单位 ───────────────────────────────────


def test_cli_zh_multi(tmp: str) -> None:
    d = os.path.join(tmp, "zh3")
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    js = write_json(os.path.join(d, "in", "meta.json"), _JSON_THREE)
    out = os.path.join(d, "out")
    r = run_convert(src, out, ["--metadata-json", js])
    m = macros(out_tex(out),
               "myfirstauthor", "mysecondauthor", "mythirdauthor",
               "myfirstaffiliation", "mysecondaffiliation", "mythirdaffiliation",
               "myfirstemail", "mysecondemail", "mythirdemail",
               "mysecondauthorEN", "mythirdauthorEN",
               "mysecondaffiliationEN", "mythirdaffiliationEN") \
        if os.path.isfile(out_tex(out)) else {}
    check("③-1 zh 3 作者 CLI exit 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    check("③-2 作者 1–3 全进对应槽位（中文 + \\kaishu）",
          m.get("myfirstauthor") == "\\kaishu 甲一"
          and m.get("mysecondauthor") == "\\kaishu 乙二"
          and m.get("mythirdauthor") == "\\kaishu 丙三", str(m))
    check("③-3 每位作者多单位：`\\\\` 连接、槽位不串位",
          m.get("myfirstaffiliation") == "甲大学" + BREAK + "甲研究院"
          and m.get("mysecondaffiliation") == "乙大学" + BREAK + "乙研究院"
          and m.get("mythirdaffiliation") == "丙大学", str(m))
    check("③-4 邮箱 1–3 各自入槽（中英页共用 my<N>email）",
          m.get("myfirstemail") == "a@example.cn"
          and m.get("mysecondemail") == "b@example.cn"
          and m.get("mythirdemail") == "c@example.cn")
    check("③-5 第 2–3 作者英文姓名/单位槽位（中文模板英文摘要页）",
          m.get("mysecondauthorEN") == "Yi Er"
          and m.get("mythirdauthorEN") == "Bing San"
          and m.get("mysecondaffiliationEN") == "B Univ" + BREAK + "B Inst"
          and m.get("mythirdaffiliationEN") == "C Univ", str(m))


# ── ③′ CLI：2 作者 / 5 作者（模板上限）各一组（多单位 + 中英槽位 + 日志）──


def test_cli_author_counts(tmp: str) -> None:
    d = os.path.join(tmp, "counts")
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    # 2 作者 × 各 2 单位，中英槽位齐全 + 日志口径
    js2 = write_json(os.path.join(d, "in", "two.json"), {
        "title_cn": "两作者测试", "title_en": "Two Authors",
        "authors": [
            {"name_cn": "甲一", "name_en": "Jia Yi",
             "affiliations_cn": ["甲大学", "甲研究院"],
             "affiliations_en": ["A Univ", "A Inst"], "email": "a@x.cn"},
            {"name_cn": "乙二", "name_en": "Yi Er",
             "affiliations_cn": ["乙大学", "乙研究院"],
             "affiliations_en": ["B Univ", "B Inst"], "email": "b@x.cn"},
        ]})
    out2 = os.path.join(d, "out2")
    r2 = run_convert(src, out2, ["--metadata-json", js2])
    m2 = macros(out_tex(out2), "mysecondauthor", "mysecondaffiliation",
                "mysecondemail", "mysecondauthorEN", "mysecondaffiliationEN") \
        if os.path.isfile(out_tex(out2)) else {}
    check("③′-1 zh 2 作者：中英槽位 + 多单位都写对",
          r2.returncode == 0
          and m2.get("mysecondauthor") == "\\kaishu 乙二"
          and m2.get("mysecondaffiliation") == "乙大学" + BREAK + "乙研究院"
          and m2.get("mysecondemail") == "b@x.cn"
          and m2.get("mysecondauthorEN") == "Yi Er"
          and m2.get("mysecondaffiliationEN") == "B Univ" + BREAK + "B Inst",
          str(m2))
    check("③′-2 2 作者日志口径：加载「2 位作者、M 项输入字段」/ 应用「写入 2 位作者」",
          re.search(r"人工元数据（--metadata-json）：2 位作者、\d+ 项输入字段",
                    r2.stdout) is not None
          and re.search(r"人工元数据：写入 2 位作者 / \d+ 个模板字段",
                        r2.stdout) is not None, r2.stdout[-700:])

    # 5 作者（模板上限 myfirstauthor…myfifthauthor）：每位 2 个单位、中英齐全
    five = {"title_cn": "五作者上限测试", "title_en": "Five Authors",
            "keywords_cn": "上限；测试",
            "authors": [
                {"name_cn": f"作者{i}", "name_en": f"Author {i}",
                 "affiliations_cn": [f"单位{i}", f"学院{i}"],
                 "affiliations_en": [f"Univ {i}", f"School {i}"],
                 "email": f"u{i}@x.cn"} for i in range(1, 6)]}
    js5 = write_json(os.path.join(d, "in", "five.json"), five)
    out5 = os.path.join(d, "out5")
    r5 = run_convert(src, out5, ["--metadata-json", js5])
    m5 = macros(out_tex(out5), "myfourthauthor", "myfourthaffiliation",
                "myfifthauthor", "myfifthaffiliation", "myfifthemail",
                "myfifthauthorEN", "myfifthaffiliationEN") \
        if os.path.isfile(out_tex(out5)) else {}
    check("③′-3 zh 5 作者（模板上限）：第 4/5 槽位 + 多单位 + 中英槽位",
          r5.returncode == 0
          and m5.get("myfourthauthor") == "\\kaishu 作者4"
          and m5.get("myfourthaffiliation") == "单位4" + BREAK + "学院4"
          and m5.get("myfifthauthor") == "\\kaishu 作者5"
          and m5.get("myfifthaffiliation") == "单位5" + BREAK + "学院5"
          and m5.get("myfifthemail") == "u5@x.cn"
          and m5.get("myfifthauthorEN") == "Author 5"
          and m5.get("myfifthaffiliationEN") == "Univ 5" + BREAK + "School 5",
          str(m5))
    check("③′-4 5 作者日志口径：加载「5 位作者」/ 应用「写入 5 位作者」",
          re.search(r"人工元数据（--metadata-json）：5 位作者、\d+ 项输入字段",
                    r5.stdout) is not None
          and re.search(r"人工元数据：写入 5 位作者 / \d+ 个模板字段",
                        r5.stdout) is not None, r5.stdout[-700:])

    out5en = os.path.join(d, "out5en")
    r5en = run_convert(src, out5en, ["--metadata-json", js5, "--lang", "en"])
    m5en = macros(out_tex(out5en), "myfifthauthor", "myfifthauthorCN",
                  "myfifthaffiliation", "myfifthaffiliationCN") \
        if os.path.isfile(out_tex(out5en)) else {}
    check("③′-5 EN 模板 5 作者：第 5 作者中英双套槽位都写对",
          r5en.returncode == 0
          and m5en.get("myfifthauthor") == "Author 5"
          and m5en.get("myfifthauthorCN") == "作者5"
          and m5en.get("myfifthaffiliation") == "Univ 5" + BREAK + "School 5"
          and m5en.get("myfifthaffiliationCN") == "单位5" + BREAK + "学院5",
          str(m5en))


# ── ④ CLI：EN 模板（中英分流 + CN 槽位 + 关键词 warning）──────────────────


def test_cli_en_multi(tmp: str) -> None:
    d = os.path.join(tmp, "en3")
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    js = write_json(os.path.join(d, "in", "meta.json"), _JSON_THREE)
    before = sha(js)
    out = os.path.join(d, "out")
    r = run_convert(src, out, ["--metadata-json", js, "--lang", "en"])
    m = macros(out_tex(out),
               "mytitle", "mytitleCN", "myabstract", "myabstractCN",
               "myfirstauthor", "myfirstauthorCN", "mysecondauthor", "mysecondauthorCN",
               "mythirdauthorCN", "myfirstaffiliation", "myfirstaffiliationCN",
               "mysecondaffiliationCN", "mykeywords") \
        if os.path.isfile(out_tex(out)) else {}
    check("④-1 EN 模板 CLI exit 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    check("④-2 EN 模板中英分流：mytitle=英文、mytitleCN=中文（摘要同）",
          m.get("mytitle") == "Three Authors Test"
          and m.get("mytitleCN") == "三位作者测试"
          and m.get("myabstract") == "Abstract A."
          and m.get("myabstractCN") == "摘要甲。", str(m))
    check("④-3 主语言（英）作者/单位进无后缀槽位，中文进 CN 后缀槽位",
          m.get("myfirstauthor") == "Jia Yi"
          and m.get("myfirstauthorCN") == "甲一"
          and m.get("mysecondauthor") == "Yi Er"
          and m.get("mysecondauthorCN") == "乙二"
          and m.get("mythirdauthorCN") == "丙三"
          and m.get("myfirstaffiliation") == "A Univ" + BREAK + "A Inst"
          and m.get("myfirstaffiliationCN") == "甲大学" + BREAK + "甲研究院"
          and m.get("mysecondaffiliationCN") == "乙大学" + BREAK + "乙研究院", str(m))
    check("④-4 EN 模板无关键词槽：不写真值 + 日志 warning",
          m.get("mykeywords") is None and "英文模板无关键词字段" in r.stdout,
          r.stdout[-400:])
    check("④-5 JSON 只读、无 slcn-* 临时残留",
          sha(js) == before
          and not glob.glob(os.path.join(tmp, "slcn-meta-*")))


# ── ⑤ CLI：Word 通道也能吃到同一份 JSON ────────────────────────────────────


def test_cli_word_channel(tmp: str) -> None:
    d = os.path.join(tmp, "word")
    docx = make_docx(["Word 稿标题", "王小明", "摘要：Word 稿摘要。",
                      "正文第一段。", "正文第二段。"],
                     os.path.join(d, "src", "paper.docx"))
    js = write_json(os.path.join(d, "in", "meta.json"), _JSON_ONE)
    out = os.path.join(d, "out")
    r = run_convert(docx, out, ["--metadata-json", js])
    m = macros(out_tex(out), "mytitle", "myfirstauthor", "myfirstaffiliation",
               "myfirstemail", "mykeywords") if os.path.isfile(out_tex(out)) else {}
    check("⑤-1 Word 通道 + --metadata-json exit 0",
          r.returncode == 0, (r.stdout + r.stderr)[-400:])
    check("⑤-2 Word 通道：JSON 人工值覆盖解析值（标题/作者/多单位/邮箱/关键词）",
          m.get("mytitle") == "测试题名"
          and m.get("myfirstauthor") == "\\kaishu 张三"
          and m.get("myfirstaffiliation")
          == "中山大学哲学系" + BREAK + "逻辑与认知研究所"
          and m.get("myfirstemail") == "zs@example.edu.cn"
          and m.get("mykeywords") == "测试；逻辑", str(m))


# ── ⑥ 优先级 + 空值不覆盖 + 空 JSON 零影响 + dry-run ───────────────────────


def test_priority_and_empty(tmp: str) -> None:
    d = os.path.join(tmp, "prio")
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    meta = write(os.path.join(d, "meta", "meta.tex"), _TEX_META)
    js_prio = write_json(os.path.join(d, "in", "prio.json"),
                         {"title_cn": "JSON题名", "abstract_cn": "JSON摘要"})
    out = os.path.join(d, "out1")
    r = run_convert(src, out,
                    ["--metadata", meta, "--title-cn", "命令行题名",
                     "--metadata-json", js_prio])
    m = macros(out_tex(out), "mytitle", "myabstract", "myfirstauthor",
               "myfirstaffiliation", "myfirstemail") \
        if os.path.isfile(out_tex(out)) else {}
    check("⑥-1 优先级：--metadata-json > --title-cn > --metadata 文件",
          r.returncode == 0 and m.get("mytitle") == "JSON题名"
          and m.get("myabstract") == "JSON摘要", str(m))
    check("⑥-2 日志说明以 JSON 为准 + 优先于 --metadata 文件",
          "以下字段以 --metadata-json 为准：mytitle" in r.stdout
          and "优先于 --metadata 元数据文件" in r.stdout, r.stdout[-600:])
    check("⑥-3 JSON 没给的字段不被清空（作者/单位/邮箱仍来自元数据文件）",
          m.get("myfirstauthor") == "{\\kaishu 王五}"
          and m.get("myfirstaffiliation") == "元数据单位"
          and m.get("myfirstemail") == "old@x.cn", str(m))

    # R4（Lead 拍板）：JSON 作者数少于源稿槽位 → 不清空后续槽位，只 warning。
    meta2 = write(os.path.join(d, "meta", "meta2.tex"), _TEX_META_TWO)
    js_one_author = write_json(os.path.join(d, "in", "one_author.json"),
                               {"authors": [{"name_cn": "甲一"}]})
    out_r4 = os.path.join(d, "out_r4")
    r_r4 = run_convert(src, out_r4, ["--metadata", meta2,
                                     "--metadata-json", js_one_author])
    check("⑥-3b R4 warning：JSON 只给 1 位作者、源稿有第 2 位 → 提示保持原样",
          r_r4.returncode == 0
          and "人工元数据只给了 1 位作者" in r_r4.stdout
          and "还有第 2 位作者的槽位" in r_r4.stdout, r_r4.stdout[-600:])

    js_empty_vals = write_json(os.path.join(d, "in", "emptyvals.json"), {
        "title_cn": "", "abstract_cn": "",
        "authors": [{"name_cn": "", "name_en": "",
                     "affiliations_cn": [], "affiliations_en": ["  "],
                     "email": ""}],
    })
    out2 = os.path.join(d, "out2")
    r2 = run_convert(src, out2, ["--metadata", meta, "--metadata-json", js_empty_vals])
    m2 = macros(out_tex(out2), "mytitle", "myfirstauthor", "myfirstemail") \
        if os.path.isfile(out_tex(out2)) else {}
    check("⑥-4 空值不覆盖：全空 JSON 视为未提供（元数据文件值原样保留）",
          r2.returncode == 0 and m2.get("mytitle") == "元数据题名"
          and m2.get("myfirstauthor") == "{\\kaishu 王五}"
          and "没有可写入的字段" in r2.stdout, (r2.stdout + r2.stderr)[-400:])

    out3 = os.path.join(d, "out3")
    r3 = run_convert(src, out3, [])
    out4 = os.path.join(d, "out4")
    r4 = run_convert(src, out4, ["--metadata-json", js_empty_vals])
    same = (r3.returncode == 0 and r4.returncode == 0
            and snapshot(os.path.join(out3, "Meta")) == snapshot(os.path.join(out4, "Meta")))
    check("⑥-5 空 JSON 参数：产物与完全不传该参数逐字节相同", same)

    out5 = os.path.join(d, "out5")
    r5 = run_convert(src, out5, ["--metadata-json", js_prio, "--dry-run"])
    check("⑥-6 --dry-run + JSON：exit 0、只打日志不写产物",
          r5.returncode == 0
          and "人工元数据（--metadata-json）：0 位作者、2 项输入字段" in r5.stdout
          and not os.path.exists(out_tex(out5)), (r5.stdout + r5.stderr)[-400:])


# ── ⑦ 缺字段占位（zh 模板 / EN 模板 CN 槽位）──────────────────────────────


def test_placeholders(tmp: str) -> None:
    d = os.path.join(tmp, "ph")
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    js = write_json(os.path.join(d, "in", "partial.json"), {
        "title_cn": "占位测试",
        "authors": [
            {"name_cn": "甲一", "name_en": "Jia Yi",
             "affiliations_cn": ["甲单位"], "affiliations_en": ["A Unit"],
             "email": "a@x.cn"},
            {"name_cn": "乙二"},
        ],
    })
    out = os.path.join(d, "out")
    r = run_convert(src, out, ["--metadata-json", js])
    tex = read(out_tex(out)) if os.path.isfile(out_tex(out)) else ""
    check("⑦-1 zh 缺字段：第二作者单位/邮箱/英文姓名/英文单位写【未识别】占位",
          r.returncode == 0
          and "【未识别：第二作者单位，请手工填写】" in tex
          and "【未识别：第二作者邮箱，请手工填写】" in tex
          and "【未识别：第二作者英文姓名，请手工填写】" in tex
          and "【未识别：第二作者英文单位，请手工填写】" in tex)
    check("⑦-2 第一作者给齐的字段不被占位符覆盖",
          "【未识别：第一作者】" not in tex and "【未识别：第一作者单位" not in tex
          and "【未识别：第一作者英文姓名" not in tex)

    out_en = os.path.join(d, "out_en")
    r2 = run_convert(src, out_en, ["--metadata-json", js, "--lang", "en"])
    tex_en = read(out_tex(out_en)) if os.path.isfile(out_tex(out_en)) else ""
    check("⑦-3 EN 模板：声明的作者缺字段 → CN/EN 槽位都写占位（默认路径不动）",
          r2.returncode == 0
          and "【未识别：第二作者中文单位，请手工填写】" in tex_en
          and "【未识别：第二作者单位，请手工填写】" in tex_en
          and "【未识别：第二作者邮箱，请手工填写】" in tex_en)


# ── ⑧ 坏输入：load_metadata_json 单元 + CLI exit 2 且不产生产物 ───────────


def test_bad_json(tmp: str) -> None:
    d = os.path.join(tmp, "bad")
    cases = {
        "syntax": (r"not json", "不是合法 JSON"),
        "toplist": ("[1, 2]", "顶层必须是 JSON 对象"),
        "authors_type": ('{"authors": "x"}', "authors 必须是数组"),
        "author_elem": ('{"authors": ["x"]}', "authors[0] 必须是对象"),
        "title_type": ('{"title_cn": 123}', "title_cn 必须是字符串"),
        "name_type": ('{"authors": [{"name_cn": 1}]}', "name_cn 必须是字符串"),
        "affil_type": ('{"authors": [{"affiliations_cn": 123}]}',
                       "必须是字符串数组"),
        "affil_elem": ('{"authors": [{"affiliations_cn": [1]}]}',
                       "affiliations_cn[0] 必须是字符串"),
    }
    for key, (text, want) in cases.items():
        p = write(os.path.join(d, "in", f"{key}.json"), text)
        try:
            C.load_metadata_json(p)
            err = ""
        except C.MetadataJSONError as e:
            err = str(e)
        check(f"⑧-1 load_metadata_json 报错（{key}）", want in err, err[:120])

    js_hole = write_json(os.path.join(d, "in", "hole.json"),
                         {"authors": [{"name_cn": "甲"}, {}, {"name_cn": "丙"}]})
    js_six = write_json(os.path.join(d, "in", "six.json"),
                        {"authors": [{"name_cn": f"作者{i}"} for i in range(6)]})
    js_ok = write_json(os.path.join(d, "in", "ok.json"), {"title_cn": "x"})
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    for key, path, want in (
        ("missing", os.path.join(d, "in", "nope.json"), "指定的文件不存在"),
        ("syntax", os.path.join(d, "in", "syntax.json"), "不是合法 JSON"),
        ("hole", js_hole, "作者槽位不能跳号"),
        ("six", js_six, "模板最多 5 位"),
    ):
        out = os.path.join(d, "out", key)
        r = run_convert(src, out, ["--metadata-json", path])
        allout = r.stdout + r.stderr
        check(f"⑧-2 CLI exit 2 + 明确报错（{key}）",
              r.returncode == 2 and want in allout and "Traceback" not in allout,
              allout[-300:])
        check(f"⑧-3 坏 JSON 不产生产物（{key}）",
              not os.path.exists(os.path.join(out, "Meta")))

    out = os.path.join(d, "out", "coop")
    r = run_convert(src, out, ["--metadata-json", js_ok, "--coop-taskbook-only"])
    stage_tex = os.path.join(out, "Meta_待核", "Meta.tex")
    m = macros(stage_tex, "mytitle") if os.path.isfile(stage_tex) else {}
    check("⑧-4 协同两段式 + JSON → 第一段接受并写进暂存 .tex（B-287 解除限制）",
          r.returncode == 0 and m.get("mytitle") == "x"
          and not os.path.exists(os.path.join(out, "Meta"))
          and "暂不支持" not in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-300:])


# ── ⑥b 批223：`--metadata-preview-out` 只读预览（表单最高优先；旧判据不变） ──


def test_b223_preview(tmp: str) -> None:
    src = write(os.path.join(tmp, "b223_src.tex"), _TEX_SRC)
    js = write_json(os.path.join(tmp, "b223_form.json"),
                    {"schema_version": 1, "title_cn": "人工题名"})
    prev = os.path.join(tmp, "b223_preview.json")
    out = os.path.join(tmp, "b223_out")
    before = sha(js)
    r = run_convert(src, out, ["--metadata-json", js, "--metadata-preview-out", prev])
    data = {}
    if os.path.isfile(prev):
        with open(prev, encoding="utf-8") as f:
            data = json.load(f)
    rows = data.get("rows") or []
    row = next((x for x in rows if x.get("field") == "title_cn"), {})
    check("⑥b-1 预览：--metadata-json 的表单值状态 = 已填（表单）",
          r.returncode == 0 and row.get("value") == "人工题名"
          and row.get("status") == "已填（表单）", str(row))
    check("⑥b-2 预览只读：不建产物目录、表单 JSON 一个字节不改",
          not os.path.isdir(out) and sha(js) == before, (r.stdout + r.stderr)[-200:])
    bad = write(os.path.join(tmp, "b223_bad.json"), "{ 坏 JSON")
    prev2 = os.path.join(tmp, "b223_preview2.json")
    r2 = run_convert(src, out, ["--metadata-json", bad, "--metadata-preview-out", prev2])
    check("⑥b-3 坏 JSON + 预览：exit 2、不写预览文件（批63b 判据不放宽）",
          r2.returncode == 2 and not os.path.isfile(prev2), (r2.stdout + r2.stderr)[-200:])


def test_b223_json_as_metadata(tmp: str) -> None:
    # B-223 FAIL-2：`--metadata x.json` 与 `--metadata-json` 同语义（两通道一致、最高优先）
    d = os.path.join(tmp, "json_as_meta")
    src_tex = write(os.path.join(d, "src", "paper.tex"), _TEX_SRC)
    js = write_json(os.path.join(d, "in", "one.json"), _JSON_ONE)
    r = run_convert(src_tex, os.path.join(d, "out"), ["--metadata", js])
    m = macros(out_tex(os.path.join(d, "out")), "mytitle") \
        if os.path.isfile(out_tex(os.path.join(d, "out"))) else {}
    check("⑥c-1 LaTeX 通道：--metadata 指向 .json 按人工元数据生效（exit 0）",
          r.returncode == 0 and m.get("mytitle") == "测试题名",
          (r.stdout + r.stderr)[-300:])
    docx = make_docx(["Word 稿标题", "王小明", "摘要：Word 稿摘要。", "正文。"],
                     os.path.join(d, "word", "paper.docx"))
    out2 = os.path.join(d, "out2")
    r2 = run_convert(docx, out2, ["--metadata", js])
    m2 = macros(out_tex(out2), "mytitle") if os.path.isfile(out_tex(out2)) else {}
    check("⑥c-2 Word 通道：--metadata 指向 .json 生效（不再「忽略且不告知」）",
          r2.returncode == 0 and m2.get("mytitle") == "测试题名"
          and "本次忽略" not in r2.stdout, (r2.stdout + r2.stderr)[-300:])
    js2 = write_json(os.path.join(d, "in", "two.json"), dict(_JSON_ONE, title_cn="后给的题名"))
    out3 = os.path.join(d, "out3")
    r3 = run_convert(src_tex, out3, ["--metadata", js, "--metadata-json", js2])
    m3 = macros(out_tex(out3), "mytitle") if os.path.isfile(out_tex(out3)) else {}
    check("⑥c-3 先到先得：--metadata-json 与 --metadata x.json 都给 title 时以 --metadata-json 为准",
          r3.returncode == 0 and m3.get("mytitle") == "后给的题名",
          (r3.stdout + r3.stderr)[-300:])


# ── ⑨ 默认零变化：无参数 / --metadata 与 HEAD 逐字节相同；kwarg None 等价 ──


def test_zero_change(tmp: str, head: str) -> None:
    d = os.path.join(tmp, "zero")
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    meta = write(os.path.join(d, "meta", "meta.tex"), _TEX_META)
    out = os.path.join(d, "out")

    new = run_convert(src, out, [])
    snap_new = snapshot(os.path.join(out, "Meta"))
    if not head:
        skip("⑨ 默认零变化（HEAD 对照）", "git archive HEAD 失败")
    else:
        shutil.rmtree(out, ignore_errors=True)
        old = run_convert(src, out, [], engine=head)
        check("⑨-1 无 --metadata-json：产物与 HEAD 逐字节相同",
              new.returncode == 0 and old.returncode == 0
              and snap_new == snapshot(os.path.join(out, "Meta")))
        check("⑨-2 无 --metadata-json：stdout 与 HEAD 逐字节相同（argv 零变化）",
              new.stdout == old.stdout, (new.stdout[-200:], old.stdout[-200:]))

        shutil.rmtree(out, ignore_errors=True)
        new2 = run_convert(src, out, ["--metadata", meta])
        snap_new2 = snapshot(os.path.join(out, "Meta"))
        shutil.rmtree(out, ignore_errors=True)
        old2 = run_convert(src, out, ["--metadata", meta], engine=head)
        check("⑨-3 --metadata .tex 路径：产物与 HEAD 逐字节相同",
              new2.returncode == 0 and old2.returncode == 0
              and snap_new2 == snapshot(os.path.join(out, "Meta")))
        check("⑨-4 --metadata .tex 路径：stdout 与 HEAD 逐字节相同",
              new2.stdout == old2.stdout)

    # 直接调用 build 入口：structured=None 与不传该关键字逐字节相同
    b1, b2 = os.path.join(d, "b1"), os.path.join(d, "b2")
    # 注意：src 目录只有 main.tex，不带前言元数据，避免自动发现干扰对比。
    B.build_latex(src, b1, basename="X", lang="zh", verbose=False)
    B.build_latex(src, b2, basename="X", lang="zh", verbose=False, structured=None)
    check("⑨-5 build_latex: structured=None 与不传参数产物逐字节相同",
          snapshot(b1) == snapshot(b2))

    docx = make_docx(["零变化测试", "王小明", "摘要：零变化。", "正文。"],
                     os.path.join(d, "w", "paper.docx"))
    w1, w2 = os.path.join(d, "w1"), os.path.join(d, "w2")
    B.build_docx(docx, w1, basename="Y", lang="zh", verbose=False,
                 proof_autofix=False)
    B.build_docx(docx, w2, basename="Y", lang="zh", verbose=False,
                 proof_autofix=False, structured=None)
    check("⑨-6 build_docx: structured=None 与不传参数产物逐字节相同",
          snapshot(w1) == snapshot(w2))


# ── ⑩ B-276：标准 LaTeX 前言回退（普通 article 稿，全合成样例）──────────────
# 普通 article 稿没有本刊模板的 \my*，只有 \title/\author/\thanks/\begin{abstract}
# 和「关键词：」行。B-276 的 texfront 只在**一个 \my* 都搬不到**时填槽；下面的
# 断言同时锁住「模板 \my* 优先」与「--metadata-json 优先级不破」。
_TEX_FRONT = r"""\documentclass{article}
\title{示例题名}
\author{{\kaishu 李雷}\thanks{示例大学哲学学院}\thanks{示例基金项目（编号：000000）资助}}
\begin{document}
\begin{abstract}
这是示例摘要。
\par\noindent\textbf{关键词：}示例关键词甲；示例关键词乙
\end{abstract}
这里是示例正文。
\end{document}
"""

_TEX_FRONT_MY = r"""\documentclass{article}
\newcommand{\mytitle}{模板题名}
\title{示例题名}
\author{李雷}
\begin{document}
这里是示例正文。
\end{document}
"""


def test_texfront_fallback(tmp: str) -> None:
    # 每个场景放独立子目录：引擎会把「源稿同目录里的完整模板稿」自动发现成元数据源
    # （identify：含大量 \my* 模板宏），同目录混放会让本组断言测到错误对象。
    d = os.path.join(tmp, "b276a")
    os.makedirs(d, exist_ok=True)
    src = write(os.path.join(d, "front.tex"), _TEX_FRONT)
    out = os.path.join(d, "out")
    r = run_convert(src, out, [])
    check("⑩-1 B-276 无 \\my* 普通稿：CLI exit 0", r.returncode == 0,
          (r.stderr or "")[-300:])
    m = macros(out_tex(out))
    check("⑩-2 texfront 回退填 mytitle", m.get("mytitle") == "示例题名",
          f"mytitle={m.get('mytitle')!r}")
    check("⑩-3 texfront 回退填 myfirstauthor（剥 \\thanks）",
          (m.get("myfirstauthor") or "").strip() == "李雷",
          f"myfirstauthor={m.get('myfirstauthor')!r}")
    check("⑩-4 texfront 回退填 myfirstaffiliation（\\thanks 单位）",
          "示例大学哲学学院" in (m.get("myfirstaffiliation") or ""))
    check("⑩-5 texfront 回退填 myabstract", "示例摘要" in (m.get("myabstract") or ""))
    check("⑩-6 texfront 回退填 mykeywords（拆出关键词行）",
          "示例关键词乙" in (m.get("mykeywords") or "")
          and "示例摘要" not in (m.get("mykeywords") or ""))
    check("⑩-7 texfront 回退填 mygrants（\\thanks 基金）",
          "示例基金项目" in (m.get("mygrants") or ""))
    # --metadata-json 优先级不破：JSON 给的值覆盖 texfront 回退值
    d2 = os.path.join(tmp, "b276b")
    os.makedirs(d2, exist_ok=True)
    src_json = write(os.path.join(d2, "front.tex"), _TEX_FRONT)
    js = write_json(os.path.join(d2, "front.json"), {
        "schema_version": 1, "title_cn": "JSON题名",
        "authors": [{"name_cn": "韩梅梅", "affiliations_cn": ["示例大学"]}],
    })
    out2 = os.path.join(d2, "out")
    r2 = run_convert(src_json, out2, ["--metadata-json", js])
    check("⑩-8 --metadata-json + 前言回退：CLI exit 0", r2.returncode == 0,
          (r2.stderr or "")[-300:])
    m2 = macros(out_tex(out2))
    check("⑩-9 --metadata-json 仍最高优先（覆盖 texfront 回退值）",
          m2.get("mytitle") == "JSON题名"
          and "韩梅梅" in (m2.get("myfirstauthor") or ""),
          f"mytitle={m2.get('mytitle')!r} author={m2.get('myfirstauthor')!r}")
    # 模板 \my* 优先：源码里既有 \my* 又有 \title 时，texfront 不得生效
    d3 = os.path.join(tmp, "b276c")
    os.makedirs(d3, exist_ok=True)
    src2 = write(os.path.join(d3, "tpl.tex"), _TEX_FRONT_MY)
    out3 = os.path.join(d3, "out")
    r3 = run_convert(src2, out3, [])
    check("⑩-10 模板 \\my* 存在时前言解析结果不被使用：CLI exit 0",
          r3.returncode == 0, (r3.stderr or "")[-300:])
    m3 = macros(out_tex(out3))
    check("⑩-11 \\my* 优先（mytitle 来自模板宏而非 \\title）",
          m3.get("mytitle") == "模板题名", f"mytitle={m3.get('mytitle')!r}")
    check("⑩-12 \\my* 存在时 \\author 不被采用（作者仍走占位符/旧链）",
          "李雷" not in (m3.get("myfirstauthor") or ""),
          f"myfirstauthor={m3.get('myfirstauthor')!r}")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="slcn_b63b_meta ")
    notes = os.path.join(tmp, "empty_notes")
    os.makedirs(notes, exist_ok=True)
    ENV["SLCN_NOTES"] = notes          # 隔离本机真实笔记仓（hermetic）
    try:
        test_unit_structured(tmp)
        test_cli_zh_one(tmp)
        test_cli_zh_multi(tmp)
        test_cli_author_counts(tmp)
        test_cli_en_multi(tmp)
        test_cli_word_channel(tmp)
        test_priority_and_empty(tmp)
        test_b223_preview(tmp)
        test_b223_json_as_metadata(tmp)
        test_placeholders(tmp)
        test_bad_json(tmp)
        test_texfront_fallback(tmp)
        head = ensure_head_engine(tmp)
        test_zero_change(tmp, head)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项"
          + (f"，SKIP {len(SKIPS)} 项" if SKIPS else ""))
    for f in FAILS:
        print("  FAIL:", f)
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
