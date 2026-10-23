#!/usr/bin/env python3
r"""第五十四批：AI 协同初排（两段式）的离线判据测试。

覆盖（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · 外部 chat 回复解析：乱序/多文件/重复键/截断/残缺/白名单外键/注释保留；
  · 状态 JSON 与指纹：源稿/元数据/输出名变了 → 拒绝续跑；ph2 重复运行幂等；
  · 低置信项：从 C 类 warning/缺字段 bib/文献区原文/元数据缺失收集；ID + 原文片段；
  · 低置信回复：JSON 校验（枚举/field 白名单/span 子串），任一不过整条拒绝；
  · 建议应用：`reference.bib.ai前` 备份、逐条记录、`restore_bib_backup()` 可逆；
  · promote 原子性：成功替换、旧目录备份、失败注入后旧目录一个字节不动；
  · CLI：默认路径逐字节不变、ph1 不写最终目录、ph2 回填交付、--coop-apply、
    --no-ai-merge 语义、指纹不符/回复全无效明确失败、预算闸退出 2。

用法：
    cd Engine && PYTHONPATH=vendor /opt/homebrew/bin/python3.14 tests/test_batch54_coop.py
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


def _entry(key: str, title: str = "测试文献", author: str = "张三",
           year: str = "2020") -> str:
    return (f"@article{{{key},\n"
            f"  author = {{{author}}},\n"
            f"  title = {{{title}}},\n"
            f"  journal = {{逻辑学研究}},\n"
            f"  year = {{{year}}},\n"
            f"}}\n")


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


# ── ① 外部 chat 回复解析 ──────────────────────────────────────────────────


def test_reply_parsing(tmp: str) -> None:
    texts = [
        ("b.md", "说明文字\n" + _entry("k2", "乙")),
        ("a.md", "% 来源：https://example.org/x\n```bibtex\n" + _entry("k1", "甲") + "```\n"),
    ]
    rep = coop.parse_replies(texts, ["k1", "k2", "k3"])
    check("①-1 乱序回复按自身键切分", sorted(rep.replies) == ["k1", "k2"])
    check("①-2 任务书里有、回复没有的键 = missing", rep.missing == ["k3"]
          and rep.status.get("k3") == "missing")
    check("①-3 条目上方 `% 来源` 注释被保留", "% 来源：https://example.org/x" in rep.replies["k1"])

    dup = coop.parse_replies([("x.md", _entry("k1") + "\n" + _entry("k1", "重复"))], ["k1"])
    check("①-4 重复键整条拒绝", "k1" not in dup.replies and "重复键" in dup.rejected.get("k1", ""))

    cut = coop.parse_replies([("cut.md", "@article{k1,\n  title = {半截}\n")], ["k1"])
    check("①-5 花括号不闭合 → 拒绝截断",
          "k1" not in cut.replies and "截断" in cut.rejected.get("k1", ""))

    bad = coop.parse_replies([("bad.md", "@article{k1,\n  title = \n}\n")], ["k1"])
    check("①-6 闭合但残缺 → 拒绝", "k1" not in bad.replies and "无法解析" in bad.rejected.get("k1", ""))

    unk = coop.parse_replies([("u.md", _entry("other"))], ["k1"])
    check("①-7 白名单外的键只进 unknown，不猜回复",
          unk.replies == {} and unk.unknown == ["other"] and unk.status.get("k1") == "missing")

    d = os.path.join(tmp, "replies_dir")
    os.makedirs(d)
    _write(os.path.join(d, "b.md"), _entry("k2"))
    _write(os.path.join(d, "a.txt"), _entry("k1"))
    _write(os.path.join(d, "ignored.bin"), "x")
    loaded = coop.load_reply_texts(d)
    check("①-8 目录按文件名排序且只收文本后缀",
          [n for n, _t in loaded] == ["a.txt", "b.md"])

    check("①-9 parse_bib_blocks 能标出未闭合条目",
          coop.parse_bib_blocks("@article{x,\n")[0].closed is False)

    check("①-10 解析报告摘要包含数字", "解析到 2 条" in rep.summary())


# ── ② 状态 / 指纹 ─────────────────────────────────────────────────────────


def test_state(tmp: str) -> None:
    src = _write(os.path.join(tmp, "src.tex"), "\\documentclass{article}\n正文\n")
    meta = _write(os.path.join(tmp, "meta.tex"), "\\title{X}\n")
    sty = _write(os.path.join(tmp, "x.sty"), "% sty\n")
    skel = _write(os.path.join(tmp, "x.tex"), "% skel\n")
    stage = os.path.join(tmp, "_待核")
    final = os.path.join(tmp, "Out")
    os.makedirs(stage)

    def params(**over):
        base = dict(source=src, metadata=meta, out_root=tmp, stem="Out", lang="zh",
                    sty=sty, skeleton=skel, verify_refs=True, no_ai_merge=True,
                    end_list_priority=False, number_cite_map=False,
                    c_class_union=True, stage_dir=stage)
        base.update(over)
        return coop.build_params(**base)

    p1 = params()
    st = coop.build_state(params=p1, taskbook_path=os.path.join(stage, "t.md"),
                          stage_dir=stage, final_dir=final, bib_keys=["k1"],
                          low_items=[{"id": "META-x", "kind": "metadata",
                                      "evidence": "x", "fields": ["value"], "need": ""}],
                          budget_article=30000, budget_batch=1000000)
    coop.save_state(stage, st)
    loaded = coop.load_state(stage)
    check("②-1 状态 JSON 可往返", loaded is not None and loaded["schema"] == coop.SCHEMA_VERSION)
    check("②-2 状态含 schema/phase/指纹/键清单/预算",
          loaded["phase"] == "ph1" and loaded["fingerprint"] == p1["fingerprint"]
          and loaded["keys"]["bib"] == ["k1"]
          and loaded["keys"]["low_confidence"] == ["META-x"]
          and loaded["budget"]["article_tokens"] == 30000)
    check("②-3 相同参数 ph2 校验通过", coop.check_ph2(loaded, p1)[0])

    p4 = params()
    p4["source_sha256"] = "0" * 64
    ok4, why4 = coop.check_ph2(loaded, p4)
    check("②-4 源稿内容变了 → 拒绝", not ok4 and "sha256" in why4)

    p5 = params()
    p5["source_sha256"] = "f" * 64
    ok5, why5 = coop.check_ph2(loaded, p5)
    check("②-5 源稿 sha256 不一致的原因可读", not ok5 and "sha256" in why5 and "重新初排" in why5)

    ok6, why6 = coop.check_ph2(loaded, params(stem="Other"))
    check("②-6 输出名改了 → 拒绝", not ok6 and "输出名" in why6)

    ok7, why7 = coop.check_ph2(loaded, params(metadata=""))
    check("②-7 元数据源改了 → 拒绝", not ok7 and "元数据" in why7)

    p8 = params(verify_refs=False)
    check("②-8 ph2 没重复写 --verify-refs 时继承 ph1（不误拒）", coop.check_ph2(loaded, p8)[0])

    p9 = params(c_class_union=False)
    ok9, _ = coop.check_ph2(loaded, p9)
    check("②-9 显式改变 C 类口径 → 拒绝", not ok9)

    check("②-10 回填指纹稳定且可区分",
          coop.replies_digest([("a", "x")]) == coop.replies_digest([("a", "x")])
          and coop.replies_digest([("a", "x")]) != coop.replies_digest([("a", "y")]))


# ── ③ 低置信项收集 / 渲染 ─────────────────────────────────────────────────


def test_low_confidence_collect(tmp: str) -> None:
    warnings = [
        "纯引用脚注 2 条无法匹配完整条目（不编造，脚注原文已保留）："
        "#12「张三 2020」；#13「李四 2021」",
        "混合型脚注 1 条（说明文字+引用，已保留脚注）：#7「说明+引用」",
        "以下编辑侧字段需人工填写：myvolnumber、mypubyear",
        "元数据源不存在，所有字段留空：x",
    ]
    items = coop.collect_low_confidence(
        warnings=warnings, missing_fields=["myfirstpage"],
        raw_ref_lines=["[1] 张三. 测试[J]. 2020."], bib_text="")
    ids = [it["id"] for it in items]
    check("③-1 C 类 warning → FN-<fid>", "FN-12" in ids and "FN-13" in ids and "FN-7" in ids)
    check("③-2 编辑侧缺失字段 → META-<字段>", "META-myvolnumber" in ids and "META-mypubyear" in ids)
    check("③-3 missing_fields → META-<字段>", "META-myfirstpage" in ids)
    check("③-4 元数据源缺失 → META-元数据源", "META-元数据源" in ids)
    check("③-5 文献区原文行 → BIB-LINE-NN", "BIB-LINE-01" in ids)
    fn = next(it for it in items if it["id"] == "FN-12")
    # 第五十七批：citation_unmatched 项多一个 match_key（窄口径可应用）；mixed_manual 仍只有 note。
    check("③-6 每项都有原文片段/允许 field（citation_unmatched 含 match_key）",
          fn["evidence"] == "张三 2020"
          and fn["fields"] == ["note", "match_key"]
          and fn["subkind"] == "citation_unmatched" and fn["target"] == ".tex")
    mixed = next(it for it in items if it["id"] == "FN-7")
    check("③-6b 混合型脚注只允许 note（不参与应用）",
          mixed["fields"] == ["note"] and mixed["subkind"] == "mixed_manual")

    bib = "".join([_entry("k1", "有题名"), "@article{k2,\n  author = {李四},\n}\n"])
    items2 = coop.collect_low_confidence(warnings=[], bib_text=bib)
    b2 = [it for it in items2 if it["id"].startswith("BIB-")]
    check("③-7 已有条目缺字段 → BIB-<key> 且列缺字段",
          any(it["id"] == "BIB-k2" and "title" in it["fields"] and "year" in it["fields"]
              for it in b2))

    sec = coop.render_low_confidence_section(items)
    check("③-8 任务书小节含 ID 与固定 JSON 格式",
          coop.SECTION_TITLE in sec and "`FN-12`" in sec
          and "verdict" in sec and "span" in sec)
    empty = coop.render_low_confidence_section([])
    check("③-9 无低置信项时也保留小节（明确说没有）", coop.SECTION_TITLE in empty and "没有发现" in empty)


# ── ④ 低置信回复校验 ──────────────────────────────────────────────────────


def test_low_confidence_parse() -> None:
    items = [
        {"id": "FN-12", "kind": "footnote", "evidence": "张三 2020",
         "fields": ["note"], "key": "", "need": "", "title": ""},
        {"id": "BIB-k1", "kind": "bib_entry", "evidence": "@article{k1}",
         "fields": ["title"], "key": "k1", "need": "", "title": ""},
    ]
    good = ('解释一段\n```json\n'
            '{"id":"FN-12","verdict":"confirmed","span":"张三 2020"}\n'
            '{"id":"BIB-k1","verdict":"corrected","field":"title",'
            '"value":"新题名","span":"@article{k1}"}\n```\n')
    r = coop.parse_low_confidence([("a.md", good)], items)
    check("④-1 合法 JSON（夹带解释/围栏）被接受", r.n_ok == 2 and r.rejected == {})
    check("④-2 修正建议带 field/value", r.suggestions["BIB-k1"][0]["value"] == "新题名")

    r2 = coop.parse_low_confidence(
        [("x", '{"id":"FN-12","verdict":"confirmed","span":"张三 2021"}')], items)
    check("④-3 span 不是原文子串 → 整条拒绝",
          "FN-12" not in r2.suggestions and "span" in r2.rejected.get("FN-12", ""))

    r3 = coop.parse_low_confidence(
        [("x", '{"id":"FN-12","verdict":"maybe","span":"张三 2020"}')], items)
    check("④-4 verdict 不在枚举 → 拒绝", "FN-12" in r3.rejected)

    r4 = coop.parse_low_confidence(
        [("x", '{"id":"BIB-k1","verdict":"corrected","field":"journal",'
              '"value":"X","span":"@article{k1}"}')], items)
    check("④-5 field 不在白名单 → 拒绝", "BIB-k1" in r4.rejected)

    r5 = coop.parse_low_confidence(
        [("x", '{"id":"FN-12","verdict":"confirmed","span":"张三 2020"}\n'
               '{"id":"FN-12","verdict":"unresolved","span":"张三 2020"}')], items)
    check("④-6 同一 ID verdict 冲突（confirmed+unresolved）→ 整条拒绝",
          "FN-12" in r5.rejected and "verdict 冲突" in r5.rejected.get("FN-12", ""))

    r6 = coop.parse_low_confidence(
        [("x", '{"id":"NOPE","verdict":"confirmed","span":"x"}')], items)
    check("④-7 白名单外 ID 只进 unknown", r6.unknown == ["NOPE"] and r6.n_ok == 0)

    r7 = coop.parse_low_confidence(
        [("x", '[{"id":"FN-12","verdict":"unresolved","span":"张三 2020"}]')], items)
    check("④-8 JSON 数组也支持", r7.n_ok == 1)


# ── ⑤ 字段改写 / 应用 / 可逆 ──────────────────────────────────────────────


def test_apply(tmp: str) -> None:
    e = "@article{k1,\n  author = {张三},\n  title = {旧题名},\n  year = {2020},\n}\n"
    e2 = coop.set_bib_field(e, "title", "新题名")
    check("⑤-1 set_bib_field 替换值且 key 不变",
          "新题名" in e2 and "@article{k1," in e2 and "author = {张三}" in e2)
    e3 = coop.set_bib_field(e, "journal", "逻辑学研究")
    check("⑤-2 set_bib_field 新增字段", "journal = {逻辑学研究}" in e3)
    e4 = coop.set_bib_field(e, "type", "book")
    check("⑤-3 set_bib_field 可改条目类型", e4.startswith("@book{k1,"))
    try:
        coop.set_bib_field(e, "title", "{不配平")
        bad_ok = True
    except ValueError:
        bad_ok = False
    check("⑤-4 不安全的字段值被拒绝", not bad_ok)

    bib = os.path.join(tmp, "reference.bib")
    _write(bib, "@article{k1,\n  author = {张三},\n  year = {2020},\n}\n")
    original = _read(bib)
    items = [{"id": "BIB-k1", "kind": "bib_entry", "evidence": "x",
              "fields": ["title"], "key": "k1", "need": "", "title": ""}]
    sug = {"BIB-k1": [{"id": "BIB-k1", "kind": "bib_entry", "verdict": "corrected",
                       "field": "title", "value": "AI 题名", "span": "x", "key": "k1"}]}
    res = coop.apply_bib_suggestions(bib, sug, items)
    check("⑤-5 应用建议写入 reference.bib", res["n_applied"] == 1 and "AI 题名" in _read(bib))
    rb = bib + ".ai前"
    check("⑤-6 应用前备份 `.ai前` 与原文逐字节相同", os.path.exists(rb) and _read(rb) == original)
    check("⑤-7 restore_bib_backup 可逆", coop.restore_bib_backup(bib) and _read(bib) == original)

    # 没有可应用建议：不碰文件、不写备份
    bib2 = os.path.join(tmp, "reference2.bib")
    _write(bib2, original)
    res2 = coop.apply_bib_suggestions(bib2, {}, items)
    check("⑤-8 无建议时文件不动、不留回滚副本",
          _read(bib2) == original and not os.path.exists(bib2 + ".ai前")
          and res2["n_applied"] == 0)

    # 建议的 span 已由 parse 校验；这里给一个 field 合法但条目不存在，应跳过
    res3 = coop.apply_bib_suggestions(
        bib2, {"BIB-k9": [{"verdict": "corrected", "field": "title",
                           "value": "x", "key": "k9"}]},
        [{"id": "BIB-k9", "kind": "bib_entry", "key": "k9", "fields": ["title"]}])
    check("⑤-9 找不到键 → 跳过且不写盘",
          _read(bib2) == original and res3["n_applied"] == 0 and not os.path.exists(bib2 + ".ai前"))


# ── ⑤b 同一 ID 多字段建议（批54 修：不再误判「重复回复」）──────────────────


def test_multi_field_suggestions(tmp: str) -> None:
    items = [{"id": "BIB-ref01", "kind": "bib_entry", "evidence": "ref01",
              "fields": ["author", "title", "year"], "key": "ref01",
              "need": "", "title": ""}]

    def obj(field_: str, value: str, verdict: str = "corrected") -> str:
        return (f'{{"id":"BIB-ref01","verdict":"{verdict}","field":"{field_}",'
                f'"value":"{value}","span":"ref01"}}')

    three = "\n".join([obj("author", "Frankfurt, Harry"),
                       obj("title", "Freedom of Will and the Concept of a Person"),
                       obj("year", "1971")])
    r = coop.parse_low_confidence([("x", three)], items)
    check("④-9 同一 ID 三个不同 field 全部接受",
          r.n_ok == 3 and len(r.suggestions.get("BIB-ref01") or []) == 3
          and not r.rejected)

    dup = coop.parse_low_confidence(
        [("x", obj("author", "A") + "\n" + obj("author", "B"))], items)
    check("④-10 同一 (id, field) 重复 → 整条拒绝并写原因",
          "BIB-ref01" not in dup.suggestions
          and "重复回复" in dup.rejected.get("BIB-ref01", "")
          and "(id, field)" in dup.rejected.get("BIB-ref01", ""))

    mix = coop.parse_low_confidence(
        [("x", obj("author", "A") + "\n"
               + '{"id":"BIB-ref01","verdict":"unresolved","field":"",'
                 '"value":"","span":"ref01"}')], items)
    check("④-11 unresolved + corrected 冲突 → 整条拒绝并写原因",
          "BIB-ref01" not in mix.suggestions
          and "verdict 冲突" in mix.rejected.get("BIB-ref01", ""))

    single = coop.parse_low_confidence([("x", obj("author", "A"))], items)
    check("④-12 单字段旧行为不回归（1 条建议照旧接受）",
          single.n_ok == 1 and single.suggestions["BIB-ref01"][0]["field"] == "author")

    # ── CLI：同 ID 三个字段 → --coop-apply 后 reference.bib 三字段都更新 ──
    src = _write(os.path.join(tmp, "multi", "src", "main.tex"), r"""\documentclass{article}
\title{多字段测试}
\author{张三}
\begin{document}
\maketitle
正文测试。

\begin{thebibliography}{9}
\bibitem{ref01} Frankfurt, Harry. Freedom of Will and the Concept of a Person[J]. 1971.
\end{thebibliography}
\end{document}
""")
    bib = _write(os.path.join(tmp, "multi", "src", "refs.bib"),
                 "@article{ref01,\n  journal = {The Journal of Philosophy},\n}\n")
    out = os.path.join(tmp, "multi", "out")
    p1 = _run(["--source", src, "--bib", bib, "--out", out, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage = os.path.join(out, "San_Zhang_待核")
    pre = _read(os.path.join(stage, "reference.bib"))
    replies = _write(os.path.join(tmp, "multi", "replies.md"), three)
    p2 = _run(["--source", src, "--out", out, "--coop-replies", replies,
               "--no-original", "--coop-apply"])
    fin = os.path.join(out, "San_Zhang")
    fb = os.path.join(fin, "reference.bib")
    final_bib = _read(fb) if os.path.isfile(fb) else ""
    check("④-13 同 ID 三字段建议 --coop-apply 后全部写入 reference.bib",
          p1.returncode == 0 and p2.returncode == 0
          and all(v in final_bib for v in
                  ("Frankfurt, Harry", "Freedom of Will and the Concept of a Person", "1971")),
          p2.stderr[-300:])
    check("④-14 --coop-apply 按 (id, field) 计数：已应用 3 条",
          "已应用 3 条" in p2.stdout)

    # B-211：promote 成功后暂存目录被清理，建议/记录等产物随交付进入最终目录
    # （`_AI协同状态.json` 除外）。
    sug_lines = [json.loads(ln) for ln in
                 _read(os.path.join(fin, "San_Zhang_AI建议.jsonl")).splitlines() if ln.strip()]
    accepted = [x for x in sug_lines if x.get("status") == "accepted"]
    check("④-15 建议 jsonl 每个 field 一行",
          len(accepted) == 3
          and {x.get("field") for x in accepted} == {"author", "title", "year"})

    rec = _read(os.path.join(fin, coop.RECORD_NAME))
    check("④-16 协同记录的应用/接受计数按条数正确",
          "应用 **3** 条" in rec and "接受 3 条" in rec)

    backups = sorted(n for n in os.listdir(fin) if n.startswith("reference.bib.ai前"))
    check("④-17 `.ai前` 只有一份且等于应用前",
          backups == ["reference.bib.ai前"] and _read(fb + ".ai前") == pre)
    check("④-18 restore_bib_backup 恢复为应用前",
          coop.restore_bib_backup(fb) and _read(fb) == pre)
    check("④-19 promote 成功后暂存目录已清理（失败才保留）",
          not os.path.exists(stage))


# ── ⑥ 原子 promote ────────────────────────────────────────────────────────


def test_promote(tmp: str) -> None:
    stage = os.path.join(tmp, "stage")
    final = os.path.join(tmp, "Final")
    os.makedirs(stage)
    _write(os.path.join(stage, "a.tex"), "A")
    _write(os.path.join(stage, coop.STATE_NAME), "{}")
    r = coop.promote_stage(stage, final)
    check("⑥-1 promote 成功且状态文件不随交付",
          os.path.isfile(os.path.join(final, "a.tex")) and not os.path.exists(os.path.join(final, coop.STATE_NAME)))
    check("⑥-2 首次交付没有旧目录备份", r.get("backup") == "")

    final2 = os.path.join(tmp, "Final2")
    os.makedirs(final2)
    _write(os.path.join(final2, "old.txt"), "OLD")
    r2 = coop.promote_stage(stage, final2)
    check("⑥-3 覆盖既有目录时旧内容进备份",
          _read(os.path.join(final2, "a.tex")) == "A"
          and os.path.isfile(os.path.join(r2["backup"], "old.txt")))

    final3 = os.path.join(tmp, "Final3")
    os.makedirs(final3)
    _write(os.path.join(final3, "keep.txt"), "KEEP")
    real_replace = os.replace

    def flaky(src: str, dst: str) -> None:
        if os.path.abspath(dst) == os.path.abspath(final3) \
                and os.path.basename(src).startswith("."):
            raise OSError("注入的交付失败")
        real_replace(src, dst)

    try:
        coop.promote_stage(stage, final3, rename=flaky)
        failed = False
    except OSError:
        failed = True
    check("⑥-4 promote 失败会抛错", failed)
    check("⑥-5 promote 失败后旧交付目录逐字节不动",
          os.path.isfile(os.path.join(final3, "keep.txt"))
          and _read(os.path.join(final3, "keep.txt")) == "KEEP")
    leftovers = [n for n in os.listdir(tmp) if ".coop-tmp-" in n]
    check("⑥-6 promote 失败清理临时目录", leftovers == [])


# ── ⑦ 预算闸（纯函数）────────────────────────────────────────────────────


def test_budget() -> None:
    est = coop.estimate_api_budget("系统" * 100, ["提示" * 100, "提示" * 200], 2048)
    check("⑦-1 估算含调用数/prompt/completion",
          est["n_calls"] == 2 and est["prompt_tokens"] > 0
          and est["completion_tokens"] == 4096 and est["total_tokens"] > 4096)
    check("⑦-2 单篇超限能报出", "单篇" in coop.budget_exceeded(est, 1, 0))
    check("⑦-3 单批超限能报出", "单批" in coop.budget_exceeded(est, 0, 1))
    check("⑦-4 0 = 不限", coop.budget_exceeded(est, 0, 0) == "")
    check("⑦-5 中文估算保守（每字至少 1/3 token）", coop.estimate_tokens("中文") >= 1)


# ── ⑧ CLI 端到端（离线、临时目录）────────────────────────────────────────


_TEX = r"""\documentclass{article}
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

_BIB = """@article{k1,
  author = {张三},
  title = {测试文献一},
  journal = {逻辑学研究},
  year = {2020},
}
@article{k2,
  author = {李四},
  title = {测试文献二},
  journal = {逻辑学研究},
  year = {2021},
}
"""


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SLCN_LLM_") and not k.startswith("SLCN_SEARCH_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), *args],
                          cwd=cwd or _ENGINE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, env=env)


def _fixture(tmp: str, *, bib: str = _BIB) -> tuple[str, str, str]:
    # 源稿放 `src/`、产物放 `out/`（与真实用法一致：产物在桌面，不在稿件目录里）。
    # 目录若混在一起，LaTeX 通道的自动前言发现会扫到上一轮产物，默认路径对比就失去意义。
    srcdir = os.path.join(tmp, "src")
    os.makedirs(srcdir, exist_ok=True)
    src = _write(os.path.join(srcdir, "main.tex"), _TEX)
    bibp = _write(os.path.join(srcdir, "refs.bib"), bib)
    out = os.path.join(tmp, "out")
    return src, bibp, out


def test_cli_default_unchanged(tmp: str) -> None:
    src, bib, out = _fixture(tmp)
    same = os.path.join(out, "same")
    a = _run(["--source", src, "--bib", bib, "--out", same,
              "--author", "张三", "--no-original"])
    fa = os.path.join(same, "San_Zhang")
    tex_a = _read(os.path.join(fa, "San_Zhang.tex"))
    bib_a = _read(os.path.join(fa, "reference.bib"))
    b = _run(["--source", src, "--bib", bib, "--out", same,
              "--author", "张三", "--no-original",
              "--ai-budget-tokens", "123456", "--ai-batch-budget-tokens", "9999999"])
    fb = os.path.join(same, "San_Zhang")
    check("⑧-1 默认转换退出 0", a.returncode == 0 and b.returncode == 0,
          f"{a.returncode}/{b.returncode}")
    check("⑧-2 只加预算参数时 .tex 逐字节不变",
          tex_a == _read(os.path.join(fb, "San_Zhang.tex")))
    check("⑧-3 只加预算参数时 reference.bib 逐字节不变",
          bib_a == _read(os.path.join(fb, "reference.bib")))
    check("⑧-4 默认路径不产生协同状态/记录",
          not os.path.exists(os.path.join(fa, coop.STATE_NAME))
          and not os.path.exists(os.path.join(fa, coop.RECORD_NAME)))


def test_cli_coop_flow(tmp: str) -> None:
    src, bib, out = _fixture(tmp)
    p1 = _run(["--source", src, "--bib", bib, "--out", out, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    check("⑧-5 ph1 退出 0", p1.returncode == 0, p1.stderr[-300:])
    check("⑧-6 ph1 不写最终交付目录", not os.path.exists(final))
    check("⑧-7 ph1 不打印『完成。产物目录』", "完成。产物目录" not in p1.stdout)
    check("⑧-8 ph1 暂存目录含任务书/状态/产物",
          all(os.path.exists(os.path.join(stage, n)) for n in
              ("San_Zhang.tex", "San_Zhang_文献核实.md", coop.STATE_NAME)))
    taskbook = _read(os.path.join(stage, "San_Zhang_文献核实.md"))
    check("⑧-9 任务书含「初排低置信项」节", "初排低置信项" in taskbook)
    st1 = json.load(open(os.path.join(stage, coop.STATE_NAME), encoding="utf-8"))
    check("⑧-10 状态为 ph1 且键清单完整",
          st1["phase"] == "ph1" and set(st1["keys"]["bib"]) == {"k1", "k2"})

    replies = os.path.join(tmp, "replies")
    os.makedirs(replies)
    _write(os.path.join(replies, "b.md"), "解释\n" + _entry("k2", "乙核实版"))
    _write(os.path.join(replies, "a.md"),
           "```bibtex\n" + _entry("k1", "甲核实版") + "```\n"
           '```json\n{"id":"META-myvolnumber","verdict":"unresolved","span":"myvolnumber"}\n```\n')
    p2 = _run(["--source", src, "--out", out, "--coop-replies", replies, "--no-original"])
    check("⑧-11 ph2 退出 0", p2.returncode == 0, p2.stderr[-400:])
    check("⑧-12 ph2 交付最终目录且含协同记录",
          os.path.isdir(final) and os.path.isfile(os.path.join(final, coop.RECORD_NAME)))
    fbib = _read(os.path.join(final, "reference.bib"))
    check("⑧-13 回填字段并入 reference.bib",
          "甲核实版" in fbib and "乙核实版" in fbib)
    check("⑧-14 状态文件不随交付进入最终目录",
          not os.path.exists(os.path.join(final, coop.STATE_NAME)))
    # B-211：状态文件不随交付且成功后暂存目录被清理；phase=done 的可见证据是
    # 交付目录里的协同记录（记录写于 promote 之后，仍进最终目录）。
    rec = _read(os.path.join(final, coop.RECORD_NAME))
    check("⑧-15 ph2 交付记录写明 phase=done 且成功后暂存目录已清理",
          "阶段：`done`" in rec and not os.path.exists(stage))

    before = _sha(os.path.join(final, "reference.bib"))
    p3 = _run(["--source", src, "--out", out, "--coop-replies", replies, "--no-original"])
    # B-211：交付成功后暂存目录已清理 → 重跑明确拒绝，不静默重交付。
    check("⑧-16 交付后重复 ph2：明确拒绝续跑（退出 2、指向第一段）",
          p3.returncode == 2
          and ("找不到可续跑的协同初排暂存目录" in p3.stderr
               or "请先运行第一段" in p3.stderr), (p3.stderr + p3.stdout)[-500:])
    check("⑧-17 重复 ph2 不改交付产物", _sha(os.path.join(final, "reference.bib")) == before)

    # 指纹不符：按原稿重新初排（暂存目录回来）→ 源稿改一个字节 → ph2 拒绝续跑，
    # 最终目录不动、暂存目录保留（失败路径不清理）。
    p1b = _run(["--source", src, "--bib", bib, "--out", out, "--author", "张三",
                "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    _write(src, _TEX + "\n% changed\n")
    p4 = _run(["--source", src, "--out", out, "--coop-replies", replies, "--no-original"])
    check("⑧-18 源稿变了 ph2 退出 2", p1b.returncode == 0 and p4.returncode == 2)
    check("⑧-19 指纹不符时明说『不一致』", "不一致" in p4.stderr or "不一致" in p4.stdout)
    check("⑧-20 指纹不符时最终产物一个字节不动且暂存目录保留",
          _sha(os.path.join(final, "reference.bib")) == before
          and os.path.isdir(stage))


def test_cli_coop_failures_and_apply(tmp: str) -> None:
    # ── 回复全无效：明确失败且不交付 ──
    base = os.path.join(tmp, "fail")
    os.makedirs(base)
    src, bib, out = _fixture(base)
    p1 = _run(["--source", src, "--bib", bib, "--out", out, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    bad = _write(os.path.join(tmp, "bad.md"), _entry("unknown"))
    p2 = _run(["--source", src, "--out", out, "--coop-replies", bad, "--no-original"])
    check("⑧-21 回复全无效 → 退出 2", p1.returncode == 0 and p2.returncode == 2)
    check("⑧-22 回复全无效 → 最终目录不存在", not os.path.exists(final))
    check("⑧-23 回复全无效 → 明说产物未改动",
          "未改动" in p2.stderr or "未改动" in p2.stdout)
    st = json.load(open(os.path.join(stage, coop.STATE_NAME), encoding="utf-8"))
    check("⑧-24 失败不推进阶段（仍 ph1）", st["phase"] == "ph1")

    # ── --no-ai-merge 语义保持：交付但不并入 ──
    good = _write(os.path.join(tmp, "good.md"), _entry("k1", "不该并入"))
    before_stage = _read(os.path.join(stage, "reference.bib"))
    p3 = _run(["--source", src, "--out", out, "--coop-replies", good, "--no-original",
               "--no-ai-merge"])
    check("⑧-25 ph2 --no-ai-merge 交付成功", p3.returncode == 0, p3.stderr[-300:])
    check("⑧-26 --no-ai-merge 时交付 bib 未被回填改动",
          _read(os.path.join(final, "reference.bib")) == before_stage)

    # ── 预算闸：直接 API 通道超限 → 打印预计、退出 2、不发起请求 ──
    tmp2 = os.path.join(tmp, "budget")
    os.makedirs(tmp2)
    src2, bib2, out2 = _fixture(tmp2)
    pb = _run(["--source", src2, "--bib", bib2, "--out", out2, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge",
               "--llm-base", "http://127.0.0.1:9/v1", "--llm-key", "x",
               "--llm-model", "mock", "--ai-budget-tokens", "1"])
    check("⑧-27 预算超限退出 2", pb.returncode == 2, pb.stderr[-300:])
    check("⑧-28 预算超限打印预计用量并说明未发请求",
          "预计" in pb.stdout and ("没有发起任何请求" in pb.stdout or "预算闸" in pb.stdout))

    # ── --coop-apply：低置信建议应用 + `.ai前` 可逆 ──
    tmp3 = os.path.join(tmp, "apply")
    os.makedirs(tmp3)
    src3 = _write(os.path.join(tmp3, "main.tex"), _TEX)
    bib3 = _write(os.path.join(tmp3, "refs.bib"), """@article{k1,
  author = {张三},
  journal = {逻辑学研究},
  year = {2020},
}
@article{k2,
  author = {李四},
  title = {测试文献二},
  journal = {逻辑学研究},
  year = {2021},
}
""")
    out3 = os.path.join(tmp3, "out")
    pa = _run(["--source", src3, "--bib", bib3, "--out", out3, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage3 = os.path.join(out3, "San_Zhang_待核")
    pre = _read(os.path.join(stage3, "reference.bib"))
    apply_reply = _write(os.path.join(tmp3, "apply.md"),
                         '```json\n{"id":"BIB-k1","verdict":"corrected","field":"title",'
                         '"value":"AI 补的题名","span":"@article{k1"}\n```\n')
    pc = _run(["--source", src3, "--out", out3, "--coop-replies", apply_reply,
               "--no-original", "--coop-apply"])
    fb = os.path.join(out3, "San_Zhang", "reference.bib")
    check("⑧-29 --coop-apply 退出 0", pa.returncode == 0 and pc.returncode == 0,
          pc.stderr[-400:])
    check("⑧-30 --coop-apply 把通过校验的建议写进产物",
          "AI 补的题名" in _read(fb))
    check("⑧-31 应用前备份 `.ai前` 即交付前的 bib", _read(fb + ".ai前") == pre)
    coop.restore_bib_backup(fb)
    check("⑧-32 restore_bib_backup 可逆（恢复为应用前）", _read(fb) == pre)


# ── main ─────────────────────────────────────────────────────────────────


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="slcn_b54_") as tmp:
        test_reply_parsing(tmp)
        test_state(tmp)
        test_low_confidence_collect(tmp)
        test_low_confidence_parse()
        test_apply(tmp)
        test_multi_field_suggestions(tmp)
        test_promote(tmp)
        test_budget()
        test_cli_default_unchanged(tmp)
        test_cli_coop_flow(tmp)
        test_cli_coop_failures_and_apply(tmp)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  FAIL:", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
