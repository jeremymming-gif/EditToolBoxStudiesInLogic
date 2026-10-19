#!/usr/bin/env python3
r"""第五十七批：`--coop-apply` 扩展 META（.tex 元数据）与 FN（窄口径脚注提升）。

覆盖（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · 低置信项 schema：C 类子类（citation_unmatched vs mixed_manual）、META 的
    target/macro/current、旧 state 兼容；
  · META 应用：写输出 .tex 的 \my* 字段、占位才写、编辑侧/漂移值不覆盖、
    值校验（空/花括号/反斜杠/数字正则）、同字段多值冲突、0 条不写盘；
  · FN 应用：仅 citation_unmatched + match_key、锚点唯一、键白名单、页码线索
    必填 locator、mixed_manual/FN-W 不改产物、歧义/非法键 skip；
  · `.ai前` 备份 + restore_tex_backup 可逆；记录逐条 target；
  · CLI：ph1→模拟回填→ph2 --coop-apply（META+FN+BIB 一次应用）、两处 `.ai前`、
    记录、stats、jsonl target、幂等、--no-ai-merge、BIB-only 旧回复不改 .tex；
  · 默认路径（无 coop 参数）不产生状态/记录/.ai前。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch57_coop_apply.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import coop  # noqa: E402

FAILS: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _item(id_: str, kind: str, *, evidence: str = "x", fields=None, key: str = "",
          subkind: str = "", target: str = "", macro: str = "",
          current: str = "") -> dict:
    return {"id": id_, "kind": kind, "title": id_, "evidence": evidence,
            "need": "", "fields": list(fields or []), "key": key,
            "status": "pending", "subkind": subkind, "target": target,
            "macro": macro, "current": current}


def _sug(id_: str, field_: str, value: str = "", *, verdict: str = "corrected",
         span: str = "x", **extra) -> dict:
    return dict({"id": id_, "kind": "", "verdict": verdict, "field": field_,
                 "value": value, "span": span, "note": ""}, **extra)


# ── ① 低置信项 schema（subkind / target / macro / current）──────────────


def test_collect_schema() -> None:
    warnings = [
        "纯引用脚注 2 条无法匹配完整条目（不编造，脚注原文已保留）："
        "#12「张三 2020」；#13「李四 2021」",
        "混合型脚注 1 条（说明文字+引用，已保留脚注）：#7「说明+引用」",
        "以下编辑侧字段需人工填写：myvolnumber、mypubyear",
        "以下字段未能从源稿解析，产物中已写「【未识别：…，请手工填写】」占位符，"
        "请手工填写或用 --title-cn/--author-cn/--affiliation-cn/--email/"
        "--abstract-cn/--title-en/--abstract-en 覆盖：第一作者单位、中文题名",
        "元数据源不存在，所有字段留空：x",
    ]
    items = coop.collect_low_confidence(
        warnings=warnings,
        missing_fields=["myfirstpage"],
        raw_ref_lines=["[1] 张三. 测试[J]. 2020."],
        bib_text="",
        label_targets={"第一作者单位": "myfirstaffiliation", "中文题名": "mytitle"},
        current_fields={"myfirstpage": "1", "myfirstaffiliation": "【未识别：x】"},
    )
    by_id = {it["id"]: it for it in items}
    fn = by_id.get("FN-12", {})
    check("①-1 纯引用脚注 → citation_unmatched + match_key 白名单",
          fn.get("subkind") == "citation_unmatched"
          and fn.get("fields") == ["note", "match_key"] and fn.get("target") == ".tex")
    check("①-2 纯引用脚注 ID/证据来自 warning",
          "FN-13" in by_id and fn.get("evidence") == "张三 2020")
    mixed = by_id.get("FN-7", {})
    check("①-3 混合型脚注 → mixed_manual，只有 note（不可应用）",
          mixed.get("subkind") == "mixed_manual" and mixed.get("fields") == ["note"])
    check("①-4 编辑侧缺失字段 → META-macro（target=.tex/macro/current）",
          by_id.get("META-myvolnumber", {}).get("macro") == "myvolnumber"
          and by_id["META-myvolnumber"]["target"] == ".tex"
          and by_id["META-myvolnumber"]["current"] == "")
    check("①-5 missing_fields → META 且记下初排取值",
          by_id.get("META-myfirstpage", {}).get("current") == "1")
    check("①-6 META-LABEL 经 label_targets 映射到宏名与初排取值",
          by_id.get("META-LABEL-1", {}).get("macro") == "myfirstaffiliation"
          and by_id["META-LABEL-1"]["current"] == "【未识别：x】")
    check("①-7 「元数据源」项不可应用（target/macro 为空）",
          by_id.get("META-元数据源", {}).get("target") == ""
          and by_id["META-元数据源"].get("macro") == "")
    check("①-8 BIB-LINE 只记录（target 为空）",
          by_id.get("BIB-LINE-01", {}).get("target") == "")
    bib = "@article{k1,\n  author = {张三},\n}\n"
    items2 = coop.collect_low_confidence(warnings=[], bib_text=bib)
    b = next(it for it in items2 if it["id"] == "BIB-k1")
    check("①-9 BIB 项 target=reference.bib", b.get("target") == "reference.bib")
    items3 = coop.collect_low_confidence(warnings=[warnings[3]])
    lab = next(it for it in items3 if it["id"].startswith("META-LABEL-"))
    check("①-10 旧调用（无 label_targets）→ LABEL 项 macro 为空、不崩",
          lab.get("macro") == "" and lab.get("current") == "")


# ── ② META 应用（unit）───────────────────────────────────────────────────


_MIN_TEX = r"""\documentclass{article}
\newcommand{\myvolnumber}{x}
\newcommand{\myissnumber}{x}
\newcommand{\myfirstpage}{1}
\newcommand{\myfirstauthor}{{\kaishu 【未识别：第一作者，请手工填写】}}
\newcommand{\myeditor}{null}
\newcommand{\mypubyear}{xxxx}
\newcommand{\receiveddate}{xxxx-xx-xx}
\begin{document}正文\end{document}
"""


def _tex_path(tmp: str, name: str = "p.tex", text: str = _MIN_TEX) -> str:
    return _write(os.path.join(tmp, name), text)


def test_meta_apply(tmp: str) -> None:
    tp = _tex_path(tmp, "meta1.tex")
    original = _read(tp)
    items = [
        _item("META-myvolnumber", "metadata", macro="myvolnumber", current="x",
              fields=["myvolnumber", "value"]),
        _item("META-LABEL-1", "metadata", macro="myfirstauthor",
              current="{\\kaishu 【未识别：第一作者，请手工填写】}", fields=["value"]),
        _item("META-myfirstpage", "metadata", macro="myfirstpage", current="1",
              fields=["myfirstpage", "value"]),
        _item("META-myeditor", "metadata", macro="myeditor", current="null",
              fields=["myeditor", "value"]),
        _item("META-mypubyear", "metadata", macro="mypubyear", current="xxxx",
              fields=["mypubyear", "value"]),
    ]
    sug = {
        "META-myvolnumber": [_sug("META-myvolnumber", "myvolnumber", "19")],
        "META-LABEL-1": [_sug("META-LABEL-1", "value", "张三")],
        "META-myfirstpage": [_sug("META-myfirstpage", "value", "137")],
        "META-myeditor": [_sug("META-myeditor", "value", "李四")],
        "META-mypubyear": [_sug("META-mypubyear", "value", "2026")],
    }
    res = coop.apply_tex_suggestions(tp, sug, items)
    tex = _read(tp)
    check("②-1 META 应用计数/跳过计数", res["n_applied"] == 5 and not res["skipped"],
          str(res.get("skipped")))
    check("②-2 输出 tex 的宏值确实被改写",
          coop.read_tex_field_values(tp).get("myvolnumber") == "19"
          and coop.read_tex_field_values(tp).get("myfirstpage") == "137"
          and coop.read_tex_field_values(tp).get("myeditor") == "李四"
          and coop.read_tex_field_values(tp).get("mypubyear") == "2026")
    check("②-3 未识别占位 → 写入真实值（保留 kaishu 包装之外的宏值）",
          "张三" in coop.read_tex_field_values(tp).get("myfirstauthor", ""))
    rb = tp + ".ai前"
    check("②-4 写 tex 前留 `.ai前` 且与原文逐字节相同",
          os.path.isfile(rb) and _read(rb) == original and res["rollback_tex"] == rb)
    check("②-5 restore_tex_backup 可逆", coop.restore_tex_backup(tp) and _read(tp) == original)
    check("②-6 无备份时 restore 返回 False",
          not coop.restore_tex_backup(os.path.join(tmp, "nope.tex")))

    # 已有编辑侧取值（初排 current 与现在不一致 = 产物被改过）→ 拒绝覆盖
    tp2 = _tex_path(tmp, "meta2.tex",
                    _MIN_TEX.replace("\\newcommand{\\myfirstpage}{1}",
                                     "\\newcommand{\\myfirstpage}{137}"))
    items2 = [_item("META-myfirstpage", "metadata", macro="myfirstpage",
                    current="1", fields=["myfirstpage", "value"])]
    r2 = coop.apply_tex_suggestions(
        tp2, {"META-myfirstpage": [_sug("META-myfirstpage", "value", "200")]}, items2)
    check("②-7 输出 tex 与初排取值不一致 → 拒绝覆盖（编辑侧值优先）",
          r2["n_applied"] == 0 and "不一致" in (r2["skipped"][0]["reason"] if r2["skipped"] else "")
          and "137" in _read(tp2))

    # 值校验：花括号 / 非数字卷号 / 非法年 / 过长
    def one(macro: str, value: str, cur: str = "x") -> dict:
        return coop.apply_tex_suggestions(
            _tex_path(tmp, f"bad_{macro}_{len(value)}.tex"),
            {f"META-{macro}": [_sug(f"META-{macro}", macro, value)]},
            [_item(f"META-{macro}", "metadata", macro=macro, current=cur,
                   fields=[macro, "value"])])

    check("②-8 值含花括号 → 拒绝",
          one("myvolnumber", "1{9}")["n_applied"] == 0)
    check("②-9 卷号非数字 → 拒绝", one("myvolnumber", "十九")["n_applied"] == 0)
    check("②-10 年份非 19xx/20xx → 拒绝", one("mypubyear", "202")["n_applied"] == 0)
    check("②-11 值含反斜杠/换行 → 拒绝",
          one("myeditor", "a\\b")["n_applied"] == 0
          and one("myeditor", "a\nb")["n_applied"] == 0)
    check("②-12 值过长 → 拒绝", one("myeditor", "长" * 1300)["n_applied"] == 0)

    # 同 macro 多建议值冲突 → 全部拒绝、不写盘
    tp3 = _tex_path(tmp, "meta3.tex")
    before = _read(tp3)
    r3 = coop.apply_tex_suggestions(
        tp3,
        {"META-myvolnumber": [_sug("META-myvolnumber", "myvolnumber", "19")],
         "META-LABEL-9": [_sug("META-LABEL-9", "value", "20")]},
        [_item("META-myvolnumber", "metadata", macro="myvolnumber", current="x"),
         _item("META-LABEL-9", "metadata", macro="myvolnumber", current="x")])
    check("②-13 同字段多值 → 全部拒绝、tex/备份都不动",
          r3["n_applied"] == 0 and _read(tp3) == before
          and not os.path.exists(tp3 + ".ai前"))

    # 非占位字段（label 项）已有真实值 → 拒绝
    tp4 = _tex_path(tmp, "meta4.tex",
                    _MIN_TEX.replace("{\\kaishu 【未识别：第一作者，请手工填写】}", "王五"))
    r4 = coop.apply_tex_suggestions(
        tp4,
        {"META-LABEL-1": [_sug("META-LABEL-1", "value", "张三")]},
        [_item("META-LABEL-1", "metadata", macro="myfirstauthor",
               current="【未识别：第一作者，请手工填写】", fields=["value"])])
    check("②-14 占位字段已有真实值 → 拒绝覆盖", r4["n_applied"] == 0 and "王五" in _read(tp4))

    # 只有 unresolved/非 corrected → 不写盘、不留副本、不报错
    tp5 = _tex_path(tmp, "meta5.tex")
    before5 = _read(tp5)
    r5 = coop.apply_tex_suggestions(
        tp5,
        {"META-myvolnumber": [_sug("META-myvolnumber", "myvolnumber", "",
                                   verdict="unresolved")]},
        [_item("META-myvolnumber", "metadata", macro="myvolnumber", current="x")])
    check("②-15 0 条可应用 → 不写盘/不留 `.ai前`/error 为空",
          r5["n_applied"] == 0 and r5["error"] == "" and _read(tp5) == before5
          and not os.path.exists(tp5 + ".ai前"))
    check("②-16 白名单外宏名 → 拒绝",
          coop.apply_tex_suggestions(
              _write(os.path.join(tmp, "meta6.tex"), "\\newcommand{\\myweird}{x}\n"),
              {"META-myweird": [_sug("META-myweird", "myweird", "y")]},
              [_item("META-myweird", "metadata", macro="myweird", current="x")],
          )["n_applied"] == 0)
    # BIB 建议不归 tex 应用器管（不产生 skip 记录）
    r7 = coop.apply_tex_suggestions(
        _write(os.path.join(tmp, "meta7.tex"), _MIN_TEX),
        {"BIB-k1": [_sug("BIB-k1", "title", "T")]},
        [_item("BIB-k1", "bib_entry", key="k1", target="reference.bib")])
    check("②-17 BIB 建议不由 tex 应用器处理（无 skip/无写盘）",
          r7["n_applied"] == 0 and not r7["skipped"]
          and not os.path.exists(os.path.join(tmp, "meta7.tex.ai前")))


# ── ③ FN 应用（unit）─────────────────────────────────────────────────────


_FN_BIB = "@article{k1,\n  author = {陈波},\n  year = {2005},\n}\n@article{k2,\n  author = {李四},\n  year = {2021},\n}\n"

_FN_TEX = r"""\documentclass{article}
\begin{document}
正文一。\footnote{陈波 2005。}
正文二。\footnote{李四 2021，第21页。}
重复锚点。\footnote{重复锚点}
再来。\footnote{重复锚点}
\end{document}
"""


def _fn_env(tmp: str, tag: str = "fn") -> tuple[str, str, list[dict]]:
    tp = _write(os.path.join(tmp, f"{tag}.tex"), _FN_TEX)
    bp = _write(os.path.join(tmp, f"{tag}.bib"), _FN_BIB)
    items = [
        _item("FN-7", "footnote", evidence="陈波 2005", subkind="citation_unmatched",
              target=".tex", fields=["note", "match_key"]),
        _item("FN-8", "footnote", evidence="李四 2021，第21页",
              subkind="citation_unmatched", target=".tex", fields=["note", "match_key"]),
        _item("FN-9", "footnote", evidence="重复锚点", subkind="citation_unmatched",
              target=".tex", fields=["note", "match_key"]),
        _item("FN-10", "footnote", evidence="说明+引用", subkind="mixed_manual",
              target=".tex", fields=["note"]),
        _item("FN-W1", "footnote", evidence="某条 warning", subkind="citation_unmatched",
              target=".tex", fields=["note", "match_key"]),
    ]
    return tp, bp, items


def test_fn_apply(tmp: str) -> None:
    tp, bp, items = _fn_env(tmp, "fn1")
    original = _read(tp)
    res = coop.apply_tex_suggestions(
        tp, {"FN-7": [_sug("FN-7", "match_key", "k1")]}, items,
        taskbook_keys=["k1", "k2"], bib_path=bp)
    tex = _read(tp)
    check("③-1 citation_unmatched + 唯一锚点 → 脚注提升为 \\parencite",
          res["n_applied"] == 1 and "\\parencite{k1}" in tex
          and "\\footnote{陈波 2005。}" not in tex)
    check("③-2 改写后 `.ai前` 等于原文、applied 记 target/键",
          res["rollback_tex"] == tp + ".ai前" and _read(tp + ".ai前") == original
          and res["applied"][0].get("target") == ".tex"
          and res["applied"][0].get("key") == "k1")
    check("③-3 restore_tex_backup 恢复脚注原文",
          coop.restore_tex_backup(tp) and _read(tp) == original)

    # locator 规则：含页码线索但没给 → 拒绝；给了 → 生成可选参数
    tp2, bp2, items2 = _fn_env(tmp, "fn2")
    r_skip = coop.apply_tex_suggestions(
        tp2, {"FN-8": [_sug("FN-8", "match_key", "k2")]}, items2,
        taskbook_keys=["k1", "k2"], bib_path=bp2)
    check("③-4 含页码线索但未给 locator → 拒绝（不丢页码）",
          r_skip["n_applied"] == 0 and any("locator" in s["reason"] for s in r_skip["skipped"])
          and "\\footnote{李四 2021，第21页。}" in _read(tp2))
    tp3, bp3, items3 = _fn_env(tmp, "fn3")
    r_loc = coop.apply_tex_suggestions(
        tp3, {"FN-8": [_sug("FN-8", "match_key", "k2", locator="21")]}, items3,
        taskbook_keys=["k1", "k2"], bib_path=bp3)
    check("③-5 有 locator → 生成 \\parencite[21]{key}",
          r_loc["n_applied"] == 1 and "\\parencite[21]{k2}" in _read(tp3))
    check("③-6 locator 非页码 → 拒绝",
          coop.apply_tex_suggestions(
              _write(os.path.join(tmp, "fn4.tex"), _FN_TEX),
              {"FN-8": [_sug("FN-8", "match_key", "k2", locator="第21页")]},
              items3, taskbook_keys=["k1", "k2"],
              bib_path=_write(os.path.join(tmp, "fn4.bib"), _FN_BIB))
          ["n_applied"] == 0)

    # 歧义锚点 / 非 match_key / mixed_manual / FN-W / 键不在白名单 / 非法键
    tp5, bp5, items5 = _fn_env(tmp, "fn5")
    r_amb = coop.apply_tex_suggestions(
        tp5, {"FN-9": [_sug("FN-9", "match_key", "k1")]}, items5,
        taskbook_keys=["k1", "k2"], bib_path=bp5)
    check("③-7 锚点命中 >1 处 → 拒绝（不改错脚注）",
          r_amb["n_applied"] == 0 and any("命中" in s["reason"] for s in r_amb["skipped"]))
    check("③-8 mixed_manual 项即使回 match_key 也不改产物",
          coop.apply_tex_suggestions(
              _write(os.path.join(tmp, "fn6.tex"), _FN_TEX),
              {"FN-10": [_sug("FN-10", "match_key", "k1")]},
              items5, taskbook_keys=["k1", "k2"],
              bib_path=_write(os.path.join(tmp, "fn6.bib"), _FN_BIB),
          )["n_applied"] == 0)
    check("③-9 FN-W 无 fid 锚点 → 拒绝",
          coop.apply_tex_suggestions(
              _write(os.path.join(tmp, "fn7.tex"), _FN_TEX),
              {"FN-W1": [_sug("FN-W1", "match_key", "k1")]},
              items5, taskbook_keys=["k1", "k2"],
              bib_path=_write(os.path.join(tmp, "fn7.bib"), _FN_BIB),
          )["n_applied"] == 0)
    tp8, bp8, items8 = _fn_env(tmp, "fn8")
    r_key = coop.apply_tex_suggestions(
        tp8, {"FN-7": [_sug("FN-7", "match_key", "k9")]}, items8,
        taskbook_keys=["k1", "k2"], bib_path=bp8)
    check("③-10 键不在任务书/bib → 拒绝",
          r_key["n_applied"] == 0 and any("不在任务书" in s["reason"] for s in r_key["skipped"]))
    check("③-11 键含非法字符 → 拒绝",
          coop.apply_tex_suggestions(
              _write(os.path.join(tmp, "fn9.tex"), _FN_TEX),
              {"FN-7": [_sug("FN-7", "match_key", "k1,x")]},
              items8, taskbook_keys=["k1", "k2"],
              bib_path=_write(os.path.join(tmp, "fn9.bib"), _FN_BIB),
          )["n_applied"] == 0)
    check("③-12 只回 note（corrected 但非 match_key）→ 不改产物",
          coop.apply_tex_suggestions(
              _write(os.path.join(tmp, "fn10.tex"), _FN_TEX),
              {"FN-7": [_sug("FN-7", "note", "这是一条引用")]},
              items8, taskbook_keys=["k1", "k2"],
              bib_path=_write(os.path.join(tmp, "fn10.bib"), _FN_BIB),
          )["n_applied"] == 0)
    # 0 条可应用 → 不写盘/不备份
    tp11, bp11, items11 = _fn_env(tmp, "fn11")
    before11 = _read(tp11)
    r_none = coop.apply_tex_suggestions(
        tp11, {"FN-7": [_sug("FN-7", "match_key", "", verdict="unresolved")]},
        items11, taskbook_keys=["k1", "k2"], bib_path=bp11)
    check("③-13 0 条可应用 → tex 不动、无 `.ai前`",
          r_none["n_applied"] == 0 and _read(tp11) == before11
          and not os.path.exists(tp11 + ".ai前"))


# ── ④ merge_apply_summaries（旧键不破，新键合并）────────────────────────


def test_merge_summaries() -> None:
    bib = {"n_applied": 2, "n_skipped": 1, "rollback": "/x/reference.bib.ai前",
           "applied": [{"id": "BIB-k1", "field": "year", "value": "2020", "key": "k1"}],
           "skipped": [{"id": "BIB-k9", "reason": "找不到"}], "error": ""}
    tex = {"n_applied": 1, "n_skipped": 2, "rollback_tex": "/x/p.tex.ai前",
           "applied": [{"id": "META-myvolnumber", "target": ".tex",
                        "field": "myvolnumber", "value": "19"}],
           "skipped": [{"id": "FN-7", "target": ".tex", "reason": "锚点失败"}], "error": ""}
    m = coop.merge_apply_summaries(bib, tex)
    check("④-1 合并计数 = 两应用器之和", m["n_applied"] == 3 and m["n_skipped"] == 3)
    check("④-2 旧 rollback 键保留 + 新增 rollback_tex", m["rollback"].endswith("reference.bib.ai前")
          and m["rollback_tex"].endswith("p.tex.ai前"))
    check("④-3 applied/skipped 逐条合并", len(m["applied"]) == 2 and len(m["skipped"]) == 2)
    check("④-4 空参数/None 安全", coop.merge_apply_summaries(None, {})["n_applied"] == 0)
    m2 = coop.merge_apply_summaries({"n_applied": 0, "error": "E1"},
                                    {"n_applied": 0, "error": "E2"})
    check("④-5 error 合并", "E1" in m2["error"] and "E2" in m2["error"])


# ── ⑤ CLI 端到端：ph1 → 模拟回填（META+FN+BIB）→ ph2 --coop-apply ───────


_TEX57 = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文测试。

\begin{thebibliography}{9}
\bibitem{k1} 张三. 测试文献一[J]. 逻辑学研究, 2020, 13(2): 1--10.
\bibitem{k2} 李四. 测试文献二[J]. 逻辑学研究, 2021, 14(3): 11--20.
\end{thebibliography}
\end{document}
"""

_BIB57 = """@article{k1,
  author = {张三},
  title = {测试文献一},
  journal = {逻辑学研究},
}
@article{k2,
  author = {李四},
  title = {测试文献二},
  journal = {逻辑学研究},
  year = {2021},
}
"""


def _run(args: list[str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SLCN_LLM_") and not k.startswith("SLCN_SEARCH_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), *args],
                          cwd=_ENGINE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, env=env)


def _fixture57(tmp: str) -> tuple[str, str, str]:
    srcdir = os.path.join(tmp, "src")
    os.makedirs(srcdir, exist_ok=True)
    src = _write(os.path.join(srcdir, "main.tex"), _TEX57)
    bibp = _write(os.path.join(srcdir, "refs.bib"), _BIB57)
    return src, bibp, os.path.join(tmp, "out")


def _ph1(src: str, bib: str, out: str) -> subprocess.CompletedProcess:
    return _run(["--source", src, "--bib", bib, "--out", out,
                 "--author", "张三", "--no-original", "--verify-refs",
                 "--no-ai-merge", "--coop-taskbook-only"])


def test_cli_e2e(tmp: str) -> None:
    base = os.path.join(tmp, "e2e")
    src, bibp, out = _fixture57(base)
    p1 = _ph1(src, bibp, out)
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    st_path = os.path.join(stage, coop.STATE_NAME)
    check("⑤-1 ph1 退出 0 且只写暂存目录",
          p1.returncode == 0 and os.path.isdir(stage) and not os.path.exists(final),
          p1.stderr[-300:])
    st = json.load(open(st_path, encoding="utf-8"))
    lows = {it["id"]: it for it in st.get("low_confidence") or []}
    meta_vol = next((it for it in lows.values()
                     if it.get("kind") == "metadata" and it.get("macro") == "myvolnumber"), None)
    meta_mail = next((it for it in lows.values()
                      if it.get("kind") == "metadata" and it.get("macro") == "myfirstemail"), None)
    bib_item = next((it for it in lows.values()
                     if it.get("kind") == "bib_entry" and it.get("key") == "k1"), None)
    check("⑤-2 ph1 低置信项含 META（macro/current）与 BIB",
          meta_vol is not None and meta_mail is not None and bib_item is not None,
          f"meta_vol={bool(meta_vol)} meta_mail={bool(meta_mail)} bib={bool(bib_item)}")

    # 模拟「ph1 渲染出的 citation_unmatched 脚注」：往暂存 tex 里加一条脚注 + 状态里加 FN 项。
    tex_stage = os.path.join(stage, "San_Zhang.tex")
    pre_tex = _read(tex_stage)
    pre_bib = _read(os.path.join(stage, "reference.bib"))
    fn_tex = pre_tex.replace("\\end{document}", "正文引用。\\footnote{陈波 2005。}\n\\end{document}")
    _write(tex_stage, fn_tex)
    st = json.load(open(st_path, encoding="utf-8"))
    st["low_confidence"].append({
        "id": "FN-7", "kind": "footnote", "title": "脚注 #7（citation_unmatched）",
        "evidence": "陈波 2005", "need": "", "fields": ["note", "match_key"],
        "key": "", "status": "pending", "subkind": "citation_unmatched",
        "target": ".tex", "macro": "", "current": ""})
    st["keys"]["low_confidence"].append("FN-7")
    st["keys"]["all"].append("FN-7")
    st["pending"].append("FN-7")
    with open(st_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
        f.write("\n")

    reply = _write(os.path.join(base, "reply.md"),
                   "```json\n"
                   f'{{"id":"{meta_vol["id"]}","verdict":"corrected",'
                   f'"field":"myvolnumber","value":"19","span":"myvolnumber"}}\n'
                   f'{{"id":"{meta_mail["id"]}","verdict":"corrected",'
                   f'"field":"value","value":"z@example.com","span":"{meta_mail["evidence"]}"}}\n'
                   f'{{"id":"{bib_item["id"]}","verdict":"corrected",'
                   f'"field":"year","value":"2020","span":"k1"}}\n'
                   '{"id":"FN-7","verdict":"corrected","field":"match_key",'
                   '"value":"k2","locator":"21","span":"陈波 2005"}\n'
                   "```\n")
    p2 = _run(["--source", src, "--out", out, "--coop-replies", reply,
               "--no-original", "--coop-apply"])
    ftex = os.path.join(final, "San_Zhang.tex")
    fvals = coop.read_tex_field_values(ftex) if os.path.isfile(ftex) else {}
    ftext = _read(ftex) if os.path.isfile(ftex) else ""
    check("⑤-3 ph2 退出 0 并交付", p2.returncode == 0 and os.path.isdir(final),
          p2.stderr[-400:])
    check("⑤-4 META 写入最终 tex（myvolnumber=19、myfirstemail 占位被填）",
          fvals.get("myvolnumber") == "19" and "z@example.com" in fvals.get("myfirstemail", ""))
    check("⑤-5 FN 脚注提升为 \\parencite[21]{k2}",
          "\\parencite[21]{k2}" in ftext and "\\footnote{陈波 2005。}" not in ftext)
    check("⑤-6 两处 `.ai前` 都随交付进最终目录",
          os.path.isfile(ftex + ".ai前")
          and os.path.isfile(os.path.join(final, "reference.bib.ai前")))
    check("⑤-7 `.ai前` 分别等于应用前的 tex/bib",
          _read(ftex + ".ai前") == fn_tex
          and _read(os.path.join(final, "reference.bib.ai前")) == pre_bib)
    rec = _read(os.path.join(final, coop.RECORD_NAME))
    check("⑤-8 协同记录逐条写 target/字段/新值 + 两份备份",
          "应用前备份：" in rec and "应用前备份（.tex）：" in rec
          and ".tex `myvolnumber`" in rec and "`match_key` ←" in rec
          and "定位 `21`" in rec)
    st2 = json.load(open(st_path, encoding="utf-8"))
    check("⑤-9 stats.low_applied 计 4 条（BIB1+FN1+META2）且 phase=done",
          st2["phase"] == "done" and st2["stats"]["low_applied"] == 4,
          str(st2.get("stats")))
    sug_lines = [json.loads(ln) for ln in
                 _read(os.path.join(final, "San_Zhang_AI建议.jsonl")).splitlines() if ln.strip()]
    accepted = [x for x in sug_lines if x.get("status") == "accepted"]
    check("⑤-10 建议 jsonl accepted 行带 target（bib/.tex）",
          {x.get("target") for x in accepted} == {"reference.bib", ".tex"},
          str([x.get("target") for x in accepted]))
    check("⑤-11 stdout 分别报文献字段与 .tex 应用条数",
          "已应用 1 条文献字段建议" in p2.stdout
          and "已应用到 .tex 3 条" in p2.stdout, p2.stdout[-400:])

    # 幂等：同回填重跑早退，产物 sha 不变
    before = (_sha(ftex), _sha(os.path.join(final, "reference.bib")))
    p3 = _run(["--source", src, "--out", out, "--coop-replies", reply,
               "--no-original", "--coop-apply"])
    check("⑤-12 重复 ph2 幂等早退且产物不动",
          p3.returncode == 0 and "幂等" in p3.stdout
          and (_sha(ftex), _sha(os.path.join(final, "reference.bib"))) == before)

    # restore 两个备份 → 回到应用前
    check("⑤-13 restore_tex_backup / restore_bib_backup 可逆",
          coop.restore_tex_backup(ftex)
          and coop.restore_bib_backup(os.path.join(final, "reference.bib"))
          and _read(ftex) == fn_tex
          and _read(os.path.join(final, "reference.bib")) == pre_bib)


def test_cli_bib_only_unchanged(tmp: str) -> None:
    """BIB-only 旧回复（批54–56 格式）：ph2 不得碰 .tex、记录保持旧格式。"""
    base = os.path.join(tmp, "bibonly")
    src, bibp, out = _fixture57(base)
    p1 = _ph1(src, bibp, out)
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    tex_stage = os.path.join(stage, "San_Zhang.tex")
    pre_tex = _read(tex_stage)
    st = json.load(open(os.path.join(stage, coop.STATE_NAME), encoding="utf-8"))
    bib_item = next(it for it in st["low_confidence"]
                    if it.get("kind") == "bib_entry" and it.get("key") == "k1")
    reply = _write(os.path.join(base, "reply.md"),
                   "```json\n"
                   f'{{"id":"{bib_item["id"]}","verdict":"corrected",'
                   '"field":"year","value":"2020","span":"k1"}\n```\n')
    p2 = _run(["--source", src, "--out", out, "--coop-replies", reply,
               "--no-original", "--coop-apply"])
    ftex = os.path.join(final, "San_Zhang.tex")
    rec = _read(os.path.join(final, coop.RECORD_NAME))
    check("⑤b-1 BIB-only 回填 ph2 退出 0",
          p1.returncode == 0 and p2.returncode == 0, p2.stderr[-300:])
    check("⑤b-2 BIB-only 时 .tex 与 ph1 逐字节相同（不被新代码碰）",
          _read(ftex) == pre_tex)
    check("⑤b-3 BIB-only 时不留 .tex 的 `.ai前`",
          not os.path.exists(ftex + ".ai前")
          and not os.path.exists(os.path.join(stage, "San_Zhang.tex.ai前")))
    check("⑤b-4 BIB-only 记录沿用旧格式（键行、不出现 .tex 备份行）",
          "（键 `k1`）" in rec and "应用前备份（.tex）" not in rec and ".tex `" not in rec)
    check("⑤b-5 BIB-only 的 bib `.ai前` 照旧存在且等于应用前",
          os.path.isfile(os.path.join(final, "reference.bib.ai前")))
    check("⑤b-6 stdout 仍报「已应用 N 条」旧口径",
          "已应用 1 条文献字段建议" in p2.stdout)


def test_cli_no_ai_merge_and_default(tmp: str) -> None:
    base = os.path.join(tmp, "nom")
    src, bibp, out = _fixture57(base)
    _ph1(src, bibp, out)
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    tex_stage = os.path.join(stage, "San_Zhang.tex")
    st = json.load(open(os.path.join(stage, coop.STATE_NAME), encoding="utf-8"))
    meta_vol = next(it for it in st["low_confidence"]
                    if it.get("kind") == "metadata" and it.get("macro") == "myvolnumber")
    reply = _write(os.path.join(base, "reply.md"),
                   "```json\n"
                   f'{{"id":"{meta_vol["id"]}","verdict":"corrected",'
                   '"field":"myvolnumber","value":"19","span":"myvolnumber"}\n```\n')
    pre_tex = _read(tex_stage)
    p2 = _run(["--source", src, "--out", out, "--coop-replies", reply,
               "--no-original", "--coop-apply", "--no-ai-merge"])
    ftex = os.path.join(final, "San_Zhang.tex")
    check("⑤c-1 --no-ai-merge 仍整体禁用 apply（.tex 不变、无 .ai前）",
          p2.returncode == 0 and _read(ftex) == pre_tex
          and not os.path.exists(ftex + ".ai前"), p2.stderr[-300:])

    # 默认路径（无任何 coop 参数）：不产生状态/记录/.ai前
    base2 = os.path.join(tmp, "plain")
    src2, bib2, out2 = _fixture57(base2)
    p = _run(["--source", src2, "--bib", bib2, "--out", out2,
              "--author", "张三", "--no-original"])
    fd = os.path.join(out2, "San_Zhang")
    check("⑤c-2 默认路径退出 0 且无 coop 状态/记录/.ai前",
          p.returncode == 0 and os.path.isfile(os.path.join(fd, "San_Zhang.tex"))
          and not os.path.exists(os.path.join(fd, coop.STATE_NAME))
          and not os.path.exists(os.path.join(fd, coop.RECORD_NAME))
          and not any(n.endswith(".ai前") for n in os.listdir(fd)))


# ── main ────────────────────────────────────────────────────────────────


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="slcn_b57_") as tmp:
        test_collect_schema()
        test_meta_apply(tmp)
        test_fn_apply(tmp)
        test_merge_summaries()
        test_cli_e2e(tmp)
        test_cli_bib_only_unchanged(tmp)
        test_cli_no_ai_merge_and_default(tmp)
    print()
    if FAILS:
        print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项")
        for f in FAILS:
            print("  FAIL:", f)
        return len(FAILS)
    print(f"断言通过 {PASS} 项，失败 0 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
