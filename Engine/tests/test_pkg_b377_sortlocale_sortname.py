#!/usr/bin/env python3
r"""B-377：排序 locale 守卫（R02）+ sortname 身份/迁移（R03）+ 条件写回（R04）+ 环境检查（R05）。

覆盖（纯内存 / 临时目录，不读真实库、不改真实库）：
  · R03：`bibdb._IDENTITY_IGNORE` 计入 `sortname`（内容摘要与字段差异都不再把它算差异）；
  · R04：`bibdbapply` 仅在库记录**显式**含 `sortname` 时写回（库没登记 → 不写不删）；
  · R02：`.blg` 五态判定（effective / degraded / no_evidence / no_blg / no_entries）+ `compile_pdf`
    集成（退化的 rc=0 也硬失败、且删掉本次 PDF）；
  · R05：`envcheck` 的 biber locale 探针（effective/degraded 两态可注入）。

用法：
    cd Engine && PYTHONPATH=$PWD/vendor /opt/homebrew/bin/python3.14 \
        tests/test_pkg_b377_sortlocale_sortname.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb, bibdbapply  # noqa: E402

_MIGRATE = os.path.join(_ENGINE, "tools", "migrate_sortname_identity.py")

PASS = 0
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


# ══════════════════════════════════════════════════════════════════════════
# R03：sortname 计入 identity-ignore（bibdb）
# ══════════════════════════════════════════════════════════════════════════
def section_r03_identity() -> None:
    print("\n── R03：sortname 不计入内容身份（bibdb）──")
    check("R03 identity-ignore 含 sortname",
          bibdb._IDENTITY_IGNORE == frozenset({"userf", "urldate", "sortname"}),
          bibdb._IDENTITY_IGNORE)
    base = {"title": "Same Title", "year": "2020"}
    sha_base = bibdb.content_sha256("book", base)
    check("R03 加/去 sortname 摘要不变",
          bibdb.content_sha256("book", {**base, "sortname": "Doe, J."}) == sha_base)
    check("R03 改 sortname 值摘要不变",
          bibdb.content_sha256("book", {**base, "sortname": "A"})
          == bibdb.content_sha256("book", {**base, "sortname": "B"}))
    check("R03 userf/urldate 仍不计入（口径不变）",
          bibdb.content_sha256("book", {**base, "userf": "cn"}) == sha_base
          and bibdb.content_sha256("book", {**base, "urldate": "2026-01-01"}) == sha_base)
    check("R03 普通字段仍计入（note 变了摘要就该变）",
          bibdb.content_sha256("book", {**base, "note": "x"}) != sha_base)
    check("R03 canonical_fields 保留 sortname（不被当元数据丢掉）",
          bibdb.canonical_fields({"sortname": "Doe, J."}) == {"sortname": "Doe, J."},
          bibdb.canonical_fields({"sortname": "Doe, J."}))
    diff = bibdb.entry_field_diff({"title": "Same Title"},
                                  {"title": "Same Title", "sortname": "Doe, J."})
    check("R03 字段差异不报 sortname", diff == {}, diff)
    diff2 = bibdb.entry_field_diff({"title": "Same Title"},
                                   {"title": "Same Title", "note": "x"})
    check("R03 字段差异仍报普通字段", "note" in diff2, diff2)


# ══════════════════════════════════════════════════════════════════════════
# R04：bibdbapply 仅在库记录显式含 sortname 时写回
# ══════════════════════════════════════════════════════════════════════════
def _safe_record(fields: dict, entry_type: str = "book") -> dict:
    return {f"k{n}": {"status": bibdb.HIT_SAFE,
                      "record": {"entry_type": entry_type, "fields": fields}}
            for n in range(1)}


def _plan_for(product_body: str, lib_fields: dict) -> tuple[list[dict], dict]:
    entries = [("book", "k0", product_body)]
    safe = {"k0": {"status": bibdb.HIT_SAFE,
                   "record": {"entry_type": "book", "fields": lib_fields}}}
    return bibdbapply.plan_library_fills(object(), entries, safe)


_BODY_BASE = "  author = {Doe, J.},\n  title = {Same Title},\n"


def section_r04_conditional_apply() -> None:
    print("\n── R04：sortname 只在库记录显式登记时写回 ──")
    check("R04 APPLY_FIELDS 仍是 28 项（不被 sortname 污染）",
          len(bibdbapply.APPLY_FIELDS) == 28 and "sortname" not in bibdbapply.APPLY_FIELDS,
          len(bibdbapply.APPLY_FIELDS))
    check("R04 条件字段常量只含 sortname",
          bibdbapply._CONDITIONAL_APPLY_FIELDS == ("sortname",),
          bibdbapply._CONDITIONAL_APPLY_FIELDS)
    check("R04 sortname 不在库导入残留集合", "sortname" not in bibdbapply._LIB_IMPORT_RESIDUE)

    # 正例 1：库显式有 sortname、产物没有 → fills 补上并落进 .bib
    plans, _stats = _plan_for(_BODY_BASE,
                              {"author": "Doe, J.", "title": "Same Title",
                               "sortname": "Doe, John"})
    check("R04 正例：库有 sortname、产物缺 → 规划 fills",
          len(plans) == 1 and plans[0]["fills"].get("sortname") == "Doe, John",
          plans)
    new_bib, applied = bibdbapply.apply_text(
        "@book{k0,\n" + _BODY_BASE + "}\n", plans)
    check("R04 正例：sortname 写进 .bib 文本", "sortname = {Doe, John}" in new_bib, new_bib)
    check("R04 正例：applied 记录 sortname",
          applied and applied[0]["filled"].get("sortname") == "Doe, John", applied)

    # 正例 2：产物有旧 sortname、库有不同值 → replaces 覆盖
    body_old = _BODY_BASE + "  sortname = {Old, Name},\n"
    plans, _stats = _plan_for(body_old,
                              {"author": "Doe, J.", "title": "Same Title",
                               "sortname": "Doe, John"})
    check("R04 正例：值不同 → replaces",
          len(plans) == 1 and plans[0]["replaces"].get("sortname") == "Doe, John",
          plans)
    new_bib, _applied = bibdbapply.apply_text("@book{k0,\n" + body_old + "}\n", plans)
    check("R04 正例：旧 sortname 被覆盖",
          "sortname = {Doe, John}" in new_bib and "Old, Name" not in new_bib, new_bib)

    # 反例 1：库没有 sortname、产物有 → 绝不写、绝不删
    body_only_sortname = _BODY_BASE + "  sortname = {Keep, Me},\n"
    plans, _stats = _plan_for(body_only_sortname,
                              {"author": "Doe, J.", "title": "Same Title"})
    check("R04 反例：库无 sortname → 规划里没有它",
          all("sortname" not in p["fills"] and "sortname" not in p["replaces"]
              for p in plans), plans)
    new_bib, _applied = bibdbapply.apply_text(
        "@book{k0,\n" + body_only_sortname + "}\n", plans)
    check("R04 反例：产物自己的 sortname 原样保留（不删）",
          "sortname = {Keep, Me}" in new_bib, new_bib)

    # 反例 2：两边都没有 → unchanged（不产生无意义 plan）
    plans, _stats = _plan_for(_BODY_BASE,
                              {"author": "Doe, J.", "title": "Same Title"})
    check("R04 反例：两边都无 → 无计划（unchanged）", plans == [], plans)

    # 反例 3：库里 sortname 是空串 → 不写空值
    plans, _stats = _plan_for(_BODY_BASE,
                              {"author": "Doe, J.", "title": "Same Title",
                               "sortname": "  "})
    check("R04 反例：库 sortname 空值 → 不写",
          all("sortname" not in p["fills"] for p in plans), plans)


# ══════════════════════════════════════════════════════════════════════════
# R02：.blg 五态判定 + compile_pdf 集成（D3：degraded 优先、rc=0 也判；D4：删 PDF）
# no_entries：`.bbl` 无 `\entry`（0 citekeys 的合法稿）→ 放行（B-377 停止条件回归守卫）
# ══════════════════════════════════════════════════════════════════════════
from slcn import convert_compile as _cc  # noqa: E402
from slcn import sortlocale as _sl  # noqa: E402

_BBL_ENTRIES = b"\\entry{cn1}{book}{}{}\\endentry\n"
_BBL_EMPTY = b"\\begin{thebibliography}{0}\n\\end{thebibliography}\n"

_BIB_CN = "@book{cn1,\n  author = {张三},\n  title = {中文标题},\n}\n"
_BIB_EN = "@book{en1,\n  author = {Doe, John},\n  title = {An English Title},\n}\n"

_BLG_EFFECTIVE = (
    b"[10] Biber.pm:123> INFO - Sorting list 'main/global//global/global/global' "
    b"of type 'entry' with template 'main' and locale 'zh__pinyin'\n")
_BLG_EFFECTIVE_SINGLE = (
    b"[10] Biber.pm:123> INFO - Sorting list 'main/global//global/global/global' "
    b"of type 'entry' with template 'main' and locale 'zh_pinyin'\n")
_BLG_DEGRADED = (
    b"[10] Biber.pm:123> INFO - Sorting list 'main/global//global/global/global' "
    b"of type 'entry' with template 'main' and locale 'xx__yy'\n"
    b"[11] Utils.pm:479> INFO - No sort tailoring available for locale 'xx__yy'\n")
_BLG_PLAIN = b"[10] Biber.pm:123> INFO - Reading 's.bcf'\n"


class _Proc:
    def __init__(self, rc: int):
        self.returncode = rc


class _FakeRun:
    """替身 subprocess.run：按第几次调用造中间产物并返回指定退出码（同 test_b290 口径）。"""

    def __init__(self, out_dir: Path, stem: str, spec: dict[int, tuple[int, dict]]):
        self.out_dir, self.stem, self.spec = out_dir, stem, spec
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(list(cmd))
        rc, files = self.spec.get(len(self.calls), (0, {}))
        for name, content in files.items():
            (self.out_dir / f"{self.stem}{name}").write_bytes(content)
        return _Proc(rc)


def _run_compile(spec: dict[int, tuple[int, dict]],
                 bib_text: str | None) -> tuple[bool, str, list[list[str]], bool]:
    """临时目录里跑一次 compile_pdf → (ok, info, 调用序列, PDF 是否残留)。"""
    old_run, old_which = _cc.subprocess.run, _cc.shutil.which
    with tempfile.TemporaryDirectory(prefix="b377_compile_") as td:
        out = Path(td)
        (out / "s.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
        if bib_text is not None:
            (out / "s.bib").write_text(bib_text, encoding="utf-8")
        fake = _FakeRun(out, "s", spec)
        try:
            _cc.subprocess.run = fake
            _cc.shutil.which = lambda _name: "/usr/bin/true"
            ok, info = _cc.compile_pdf(str(out), "s")
            pdf_left = (out / "s.pdf").exists()
        finally:
            _cc.subprocess.run, _cc.shutil.which = old_run, old_which
        return ok, info, fake.calls, pdf_left


def _verdict(blg: bytes | None, *, cjk: bool, entries: bool = True) -> dict:
    with tempfile.TemporaryDirectory(prefix="b377_blg_") as td:
        path = Path(td) / "s.blg"
        if blg is not None:
            path.write_bytes(blg)
        return _sl.blg_locale_verdict(str(path), bib_has_cjk=cjk, bbl_entries=entries)


def section_r02_locale_guard() -> None:
    print("\n── R02：.blg 五态判定（effective/degraded/no_evidence/no_blg/no_entries）──")
    v = _verdict(_BLG_EFFECTIVE, cjk=True)
    check("R02 effective 双下划线 → ok/state/locale",
          v["ok"] and v["state"] == "effective" and v["locale"] == "zh__pinyin", v)
    check("R02 effective evidence 是排序行原文",
          "and locale 'zh__pinyin'" in v["evidence"] and "\n" not in v["evidence"], v["evidence"])
    v = _verdict(_BLG_EFFECTIVE_SINGLE, cjk=True)
    check("R02 effective 单下划线（真实 -l=zh_pinyin 输出）也算",
          v["ok"] and v["state"] == "effective" and v["locale"] == "zh_pinyin", v)
    v = _verdict(_BLG_DEGRADED, cjk=True)
    check("R02 degraded → ok=False/state/命中 locale",
          not v["ok"] and v["state"] == "degraded" and v["locale"] == "xx__yy", v)
    check("R02 degraded detail 含「排序 locale 未生效」与原始行",
          "排序 locale 未生效" in v["detail"]
          and "No sort tailoring available for locale 'xx__yy'" in v["detail"], v["detail"])
    check("R02 degraded evidence 给 No sort tailoring 行（原始行含 biber 前缀）",
          "No sort tailoring available for locale 'xx__yy'" in v["evidence"]
          and "\n" not in v["evidence"], v["evidence"])
    # degraded 优先：退化行与生效行同时存在也必须判 degraded（静默退回 en_US 的证据更硬）
    both = _BLG_EFFECTIVE + _BLG_DEGRADED
    v = _verdict(both, cjk=True)
    check("R02 degraded 优先于 effective",
          not v["ok"] and v["state"] == "degraded", v)
    v = _verdict(_BLG_PLAIN, cjk=True)
    check("R02 no_evidence + 中文 → 硬失败",
          not v["ok"] and v["state"] == "no_evidence"
          and ".blg 无 zh__pinyin 排序生效证据且参考文献含中文条目" == v["detail"], v)
    v = _verdict(_BLG_PLAIN, cjk=False)
    check("R02 no_evidence + 无中文 → 放行",
          v["ok"] and v["state"] == "no_evidence", v)
    v = _verdict(None, cjk=True)
    check("R02 无 .blg + 中文 → 硬失败（no_blg）",
          not v["ok"] and v["state"] == "no_blg" and "没有 .blg" in v["detail"], v)
    v = _verdict(None, cjk=False)
    check("R02 无 .blg + 无中文 → 放行（no_blg）",
          v["ok"] and v["state"] == "no_blg" and "无中文条目，放行" in v["detail"], v)
    v = _verdict(b"  \n\n", cjk=True)
    check("R02 空 .blg 等同 no_blg（有中文硬失败）",
          not v["ok"] and v["state"] == "no_blg", v)
    v = _verdict(_BLG_PLAIN, cjk=True, entries=False)
    check("R02 no_entries（.bbl 无 \\entry）+ 中文 bib → 放行",
          v["ok"] and v["state"] == "no_entries" and "无需排序 locale" in v["detail"], v)
    v = _verdict(_BLG_DEGRADED, cjk=True, entries=False)
    check("R02 no_entries 优先于 degraded（没东西可排时不误伤）",
          v["ok"] and v["state"] == "no_entries", v)

    # bib_has_cjk：顶层 *.bib、命中中日文即 True；无 bib / 全 ASCII → False
    with tempfile.TemporaryDirectory(prefix="b377_bib_") as td:
        root = Path(td)
        check("R02 无 .bib → bib_has_cjk False", _sl.bib_has_cjk(str(root)) is False)
        (root / "a.bib").write_text(_BIB_EN, encoding="utf-8")
        check("R02 全英文 .bib → False", _sl.bib_has_cjk(str(root)) is False)
        (root / "b.bib").write_text(_BIB_CN, encoding="utf-8")
        check("R02 有中文 .bib → True", _sl.bib_has_cjk(str(root)) is True)
        (root / "b.bib").unlink()
        (root / "c.bib").write_text("@book{j,\n title={カナ},\n}\n", encoding="utf-8")
        check("R02 假名也算 CJK → True", _sl.bib_has_cjk(str(root)) is True)

    print("\n── R02：compile_pdf 集成（fake subprocess，不跑真 xelatex/biber）──")
    # 退化 + rc=0（真实故障形态）→ False、信息带 No sort tailoring、假 PDF 被删
    spec_deg = {
        1: (0, {".bcf": b"</bcf:controlfile>\n", ".pdf": b"%PDF-1.4\n"}),
        2: (0, {".bbl": _BBL_ENTRIES, ".blg": _BLG_DEGRADED}),
    }
    ok, info, calls, pdf_left = _run_compile(spec_deg, _BIB_CN)
    check("R02 集成：degraded + rc=0 → compile_pdf False", not ok, info)
    check("R02 集成：失败文案含 No sort tailoring 与 .blg 指引",
          "No sort tailoring available for locale 'xx__yy'" in info
          and "见 s.blg" in info, info)
    check("R02 集成：degraded 时不留假 PDF", not pdf_left)
    check("R02 集成：degraded 时 biber 后立即停（未继续 xelatex）", len(calls) == 2, calls)

    # 退化 + rc=2 + .bbl 非空：locale 判定先于「非零但产物完好」的容错
    spec_deg2 = {
        1: (0, {".bcf": b"x"}),
        2: (2, {".bbl": _BBL_ENTRIES, ".blg": _BLG_DEGRADED}),
    }
    ok, info, _calls, _pdf = _run_compile(spec_deg2, _BIB_CN)
    check("R02 集成：degraded + rc=2 → locale 失败优先（不是「biber 第 2 步失败」）",
          not ok and "排序 locale 未生效" in info and "biber 第 2 步失败" not in info, info)

    # effective → 照常走完 4 步成功
    spec_ok = {
        1: (0, {".bcf": b"</bcf:controlfile>\n"}),
        2: (0, {".bbl": _BBL_ENTRIES, ".blg": _BLG_EFFECTIVE}),
        4: (0, {".pdf": b"%PDF-1.4\n"}),
    }
    ok, info, calls, pdf_left = _run_compile(spec_ok, _BIB_CN)
    check("R02 集成：effective + 中文 → 成功", ok and pdf_left, info)
    check("R02 集成：effective 走满 4 步", len(calls) == 4, calls)

    # no_evidence + 中文 → 硬失败且删 PDF
    spec_nc = {
        1: (0, {".bcf": b"x", ".pdf": b"%PDF-1.4\n"}),
        2: (0, {".bbl": _BBL_ENTRIES, ".blg": _BLG_PLAIN}),
    }
    ok, info, _calls, pdf_left = _run_compile(spec_nc, _BIB_CN)
    check("R02 集成：no_evidence + 中文 → False 且无 PDF",
          not ok and not pdf_left and ".blg 无 zh__pinyin 排序生效证据" in info, info)

    # no_evidence + 无中文（合成稿/英文稿）→ 放行（既有无中文口径不变）
    spec_ne = {
        1: (0, {".bcf": b"x"}),
        2: (0, {".bbl": _BBL_ENTRIES, ".blg": _BLG_PLAIN}),
        4: (0, {".pdf": b"%PDF-1.4\n"}),
    }
    ok, info, _calls, pdf_left = _run_compile(spec_ne, _BIB_EN)
    check("R02 集成：no_evidence + 无中文 → 放行", ok and pdf_left, info)

    # 无 .bcf（无参考文献稿）→ 完全走原逻辑，退化 .blg 也不能误伤
    spec_nobcf = {
        2: (2, {".blg": _BLG_DEGRADED}),
        4: (0, {".pdf": b"%PDF-1.4\n"}),
    }
    ok, info, _calls, _pdf = _run_compile(spec_nobcf, _BIB_CN)
    check("R02 集成：无 .bcf → 保留原放行语义（不看 locale）", ok, info)

    # .blg 缺失 + 中文 → no_blg 硬失败（rc=0 也一样）
    spec_nobl = {
        1: (0, {".bcf": b"x"}),
        2: (0, {".bbl": _BBL_ENTRIES}),
    }
    ok, info, _calls, _pdf = _run_compile(spec_nobl, _BIB_CN)
    check("R02 集成：.blg 缺失 + 中文 → False",
          not ok and "没有 .blg" in info, info)

    # 回归守卫（B-377 停止条件）：稿件仓 EN_template.tex 有 reference.bib（含中文）但 0 个
    # \cite → .bbl 无条目；base 可编译（PDF 80593B），candidate 不得把「有 bib 无引用」稿打成失败。
    spec_noent = {
        1: (0, {".bcf": b"x"}),
        2: (0, {".bbl": _BBL_EMPTY, ".blg": _BLG_PLAIN}),
        4: (0, {".pdf": b"%PDF-1.4\n"}),
    }
    ok, info, calls, pdf_left = _run_compile(spec_noent, _BIB_CN)
    check("R02 集成：0 citekeys（.bbl 无条目）+ 中文 bib → 仍可编译",
          ok and pdf_left and len(calls) == 4, info)


# ══════════════════════════════════════════════════════════════════════════
# R03：一次性迁移脚本 tools/migrate_sortname_identity.py（迷你库自测）
# ══════════════════════════════════════════════════════════════════════════
def _old_identity_sha(entry_type: str, fields: dict) -> str:
    """迁移前的旧算法：`_IDENTITY_IGNORE` 只有 userf/urldate（sortname 计入）。"""
    canon = bibdb.canonical_fields(fields)
    payload = [entry_type.lower(), [[n, v] for n, v in sorted(canon.items())]]
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _mk_record(key: str, fields: dict, sha: str,
               updated_at: str = "2026-01-01T00:00:00+08:00") -> dict:
    return {"version": bibdb.SCHEMA_VERSION, "key": key, "entry_type": "book",
            "fields": fields, "verified": False, "content_sha256": sha,
            "aliases": [], "sources": [{"kind": "seed", "imported_at": updated_at}],
            "conflicts": [], "created_at": updated_at, "updated_at": updated_at}


def _write_db(path: Path, records: list[dict]) -> None:
    path.write_text("".join(bibdb._record_line(r) + "\n" for r in records),
                    encoding="utf-8")


def _run_migrate(args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (_ENGINE, os.path.join(_ENGINE, "vendor"),
                    env.get("PYTHONPATH", "")) if p)
    return subprocess.run([sys.executable, _MIGRATE, *args], cwd=_ENGINE, env=env,
                          capture_output=True, text=True)


def section_r03_migration() -> None:
    print("\n── R03：迁移脚本（mini 库 dry-run/apply/幂等/CLI 守卫）──")
    with tempfile.TemporaryDirectory(prefix="b377_migrate_") as td:
        root = Path(td)
        db = root / "db.jsonl"
        fields_sort = {"title": "T1", "sortname": "Zeta, A."}
        fields_userf = {"title": "T2", "userf": "cn"}
        fields_plain = {"title": "T3"}
        fields_urldate = {"title": "T4", "urldate": "2026-01-02"}
        records = [
            _mk_record("a_sortname", fields_sort, _old_identity_sha("book", fields_sort)),
            _mk_record("b_userf", fields_userf,
                       bibdb.content_sha256("book", fields_userf)),
            _mk_record("c_plain", fields_plain,
                       bibdb.content_sha256("book", fields_plain)),
            _mk_record("d_urldate", fields_urldate,
                       bibdb.content_sha256("book", fields_urldate)),
        ]
        _write_db(db, records)
        before_bytes = db.read_bytes()
        before_sha = bibdb.db_sha256(str(db))
        out = root / "dry"
        proc = _run_migrate(["--db", str(db), "--dry-run", "--out", str(out)])
        check("R03 迁移 dry-run rc=0", proc.returncode == 0, proc.stderr)
        csv_path = out / "sortname_identity_dryrun.csv"
        check("R03 迁移 dry-run 产出 CSV", csv_path.is_file())
        with open(csv_path, encoding="utf-8", newline="") as fh:
            csv_rows = list(csv.DictReader(fh))
        check("R03 迁移 CSV 列名符合约定",
              csv_rows and tuple(csv_rows[0].keys())
              == ("key", "entry_type", "old_sha256", "new_sha256", "changed"),
              csv_rows[:1])
        check("R03 迁移 CSV 覆盖全部记录", len(csv_rows) == 4, len(csv_rows))
        check("R03 迁移 changed 恰为显式 sortname 那条",
              [r["key"] for r in csv_rows if r["changed"] == "True"] == ["a_sortname"],
              [(r["key"], r["changed"]) for r in csv_rows])
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        check("R03 迁移 summary 计数 records/changed/unchanged",
              summary["records"] == 4 and summary["changed"] == 1
              and summary["unchanged"] == 3 and summary["errors"] == [], summary)
        check("R03 迁移 summary 的 changed==显式 sortname 数",
              summary["explicit_sortname_records"] == 1
              and summary["changed_equals_explicit_sortname"] is True, summary)
        check("R03 迁移 dry-run 前后库 sha 不变",
              summary["db_sha256_before"] == before_sha
              and summary["db_sha256_after"] == before_sha, summary["db_sha256_after"])
        check("R03 迁移 dry-run 一个字节都不写库", db.read_bytes() == before_bytes)
        check("R03 迁移 summary 写明只改 sha/updated_at、fields 不动",
              "fields 及其它字段逐字节不变" in summary["note"], summary["note"])

        # apply：只改 a 一条（sha + updated_at），其余行逐字节不变；再跑幂等
        out2 = root / "apply"
        proc = _run_migrate(["--db", str(db), "--apply", "--out", str(out2)])
        check("R03 迁移 apply rc=0", proc.returncode == 0, proc.stderr)
        db_after_sha = bibdb.db_sha256(str(db))
        check("R03 迁移 apply 后库 sha 变化", db_after_sha != before_sha)
        store = bibdb.Store.load(str(db), lenient=True)
        by_key = {str(r["key"]): r for r in store.records()}
        expected = bibdb.content_sha256("book", fields_sort)
        check("R03 迁移 apply 后 sortname 记录 sha=现行算法",
              by_key["a_sortname"]["content_sha256"] == expected)
        check("R03 迁移 apply 后 sortname 记录 updated_at 更新",
              by_key["a_sortname"]["updated_at"] != "2026-01-01T00:00:00+08:00",
              by_key["a_sortname"]["updated_at"])
        check("R03 迁移 apply 后 userf/urldate/普通记录 sha 不变、updated_at 不变",
              all(by_key[k]["content_sha256"] == rec["content_sha256"]
                  and by_key[k]["updated_at"] == rec["updated_at"]
                  for k, rec in [("b_userf", records[1]), ("c_plain", records[2]),
                                 ("d_urldate", records[3])]), "b/c/d")
        check("R03 迁移 apply 后 fields 无增删改（4 条全同）",
              all(by_key[str(r["key"])]["fields"] == r["fields"] for r in records))
        old_lines = before_bytes.decode("utf-8").splitlines()
        new_lines = db.read_text(encoding="utf-8").splitlines()
        diff_idx = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        check("R03 迁移 apply 只重写了变化的那一条行（行序保持）",
              len(old_lines) == len(new_lines) and diff_idx == [0], diff_idx)
        check("R03 迁移 apply 后库无结构问题", store.problems == [], store.problems)

        out3 = root / "apply2"
        proc = _run_migrate(["--db", str(db), "--apply", "--out", str(out3)])
        summary3 = json.loads((out3 / "summary.json").read_text(encoding="utf-8"))
        check("R03 迁移二次 apply changed=0（幂等）", summary3["changed"] == 0, summary3)
        check("R03 迁移二次 apply 不重写库",
              bibdb.db_sha256(str(db)) == db_after_sha)
        check("R03 迁移 apply 产出摘要 CSV",
              (out2 / "sortname_identity_apply.csv").is_file())

        # CLI 守卫：--db 必填、模式二选一、库不存在
        check("R03 迁移 --db 必填（缺 → rc2）",
              _run_migrate(["--dry-run"]).returncode == 2)
        check("R03 迁移 必须显式 --dry-run/--apply（缺 → rc2）",
              _run_migrate(["--db", str(db)]).returncode == 2)
        check("R03 迁移 --dry-run 与 --apply 互斥",
              _run_migrate(["--db", str(db), "--dry-run", "--apply"]).returncode == 2)
        missing = _run_migrate(["--db", str(root / "nope.jsonl"), "--dry-run"])
        check("R03 迁移 库不存在 → rc2 + 明确文案",
              missing.returncode == 2 and "引用库不存在" in missing.stderr, missing.stderr)

        # 有非 sha 结构问题 → 拒绝写回（防写回时丢行）
        bad = root / "bad.jsonl"
        bad.write_bytes(before_bytes + b"{not json}\n")
        bad_before = bad.read_bytes()
        proc = _run_migrate(["--db", str(bad), "--apply", "--out", str(root / "badout")])
        check("R03 迁移 非 sha 结构问题 → 拒绝 apply",
              proc.returncode == 2 and "结构问题" in proc.stderr, proc.stderr)
        check("R03 迁移 拒绝时库一个字节不动", bad.read_bytes() == bad_before)


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    section_r03_identity()
    section_r03_migration()
    section_r04_conditional_apply()
    section_r02_locale_guard()
    print()
    print(f"合计：PASS {PASS} / FAIL {len(FAILS)}")
    if FAILS:
        print("FAILED: " + "; ".join(FAILS))
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
