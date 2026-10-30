#!/usr/bin/env python3
r"""B-287：两段式协同 × 人工元数据（`--metadata-json` / `--metadata *.json`）。

背景（spec `drafts/specs/b287-coop-metadata.md`，判据 C1–C5/C7）：
第一段（`--coop-taskbook-only`）接受人工元数据 JSON，值写进**暂存** `.tex`，
并在状态文件里记下可选 `structured_metadata`（schema/origin/values/sha256）；
第二段（`--coop-replies`）从暂存状态继承，不带 JSON 也保留人工值；显式带
JSON 时必须与初排 sha256 一致，改过/旧状态一律 exit 2 并指向「重跑第一段」。

全部离线、hermetic：`SLCN_NOTES` 指向临时空目录（不读真实 bib_db），
fixtures 全为虚构样例。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b287_coop_metadata.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import coop  # noqa: E402
from slcn import latex as _latex  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0
ENV: dict = {}


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
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def make_env(notes_dir: str) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES",
                           "SLCN_MANUSCRIPTS")):
            env.pop(key, None)
    env["SLCN_NOTES"] = notes_dir
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return env


_HEAD_ENGINES: dict[str, str] = {}


def ensure_head_engine(tmp: str) -> str:
    """`git archive HEAD` → 改动前（HEAD）的引擎，供默认零变化对照（每次运行只解包一次）。"""
    if tmp in _HEAD_ENGINES:
        return _HEAD_ENGINES[tmp]
    try:
        tar = subprocess.run(["git", "-C", _REPO, "archive", "HEAD"],
                             capture_output=True, timeout=120)
        if tar.returncode != 0:
            return ""
        dest = tempfile.mkdtemp(prefix="head_", dir=tmp)
        p = os.path.join(tmp, "head.tar")
        with open(p, "wb") as f:
            f.write(tar.stdout)
        with tarfile.open(p) as tf:
            tf.extractall(dest)
        eng = os.path.join(dest, "Engine")
        if not os.path.isfile(os.path.join(eng, "convert.py")):
            return ""
        _HEAD_ENGINES[tmp] = eng
        return eng
    except Exception:  # noqa: BLE001
        return ""


def run_convert(src: str, out: str, extra: list[str], *,
                engine: str = _ENGINE) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(engine, "convert.py"),
           "--source", src, "--out", out, "--name", "Meta", "--no-original"]
    cmd += extra
    return subprocess.run(cmd, cwd=_ENGINE, env=ENV, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def out_tex(out: str) -> str:
    return os.path.join(out, "Meta", "Meta.tex")


def staged_tex(out: str) -> str:
    return os.path.join(out, "Meta_待核", "Meta.tex")


def macros(tex_path: str, *names: str) -> dict[str, str]:
    text = read(tex_path)
    if not names:
        return {n: m.value for n, m in _latex.parse_macros(text).items()}
    return {n: _latex.get_macro_value(text, n) for n in names}


def read_state(out: str) -> dict:
    p = coop.state_path(os.path.join(out, "Meta_待核"))
    return json.loads(read(p)) if os.path.isfile(p) else {}


# ── 虚构 fixtures（不来自任何真实稿件）────────────────────────────────────
_TEX_SRC = r"""\documentclass{article}
\begin{document}
这里是中文正文测试。
\end{document}
"""

_BIB = """@article{k1,
  author = {王五},
  title = {测试文献一},
  journal = {逻辑学研究},
  year = {2020},
}
"""

# 人工元数据：题名/两位作者/单位/邮箱/关键词（name_en 故意缺，只验 CN 槽位）。
_FORM = {
    "schema_version": 1,
    "title_cn": "人工题名",
    "keywords_cn": "甲；乙",
    "authors": [
        {"name_cn": "张三", "affiliations_cn": ["甲大学"], "email": "z@x.cn"},
        {"name_cn": "李四", "affiliations_cn": ["乙大学"]},
    ],
}

_REPLY = '```json\n{"id": "META-myvolnumber", "verdict": "unresolved", "span": "myvolnumber"}\n```\n'


def _fixture(tmp: str, tag: str) -> tuple[str, str, str]:
    d = os.path.join(tmp, tag)
    src = write(os.path.join(d, "src", "main.tex"), _TEX_SRC)
    bib = write(os.path.join(d, "src", "refs.bib"), _BIB)
    return src, bib, os.path.join(d, "out")


def _reply_dir(tmp: str, tag: str) -> str:
    d = os.path.join(tmp, tag, "replies")
    os.makedirs(d, exist_ok=True)
    write(os.path.join(d, "a.md"), _REPLY)
    return d


# ── C1：第一段接受 JSON，值进暂存 .tex + 状态记 structured_metadata ────────


def test_c1_ph1_with_json(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "c1")
    form = write_json(os.path.join(tmp, "c1", "form.json"), _FORM)
    r = run_convert(src, out, ["--bib", bib, "--verify-refs",
                               "--coop-taskbook-only", "--metadata-json", form])
    final = os.path.join(out, "Meta")
    check("C1-1 ph1 + --metadata-json 退出 0", r.returncode == 0,
          (r.stdout + r.stderr)[-400:])
    check("C1-2 ph1 仍不写最终交付目录", not os.path.exists(final))
    stage = os.path.join(out, "Meta_待核")
    check("C1-3 暂存目录含 .tex 与状态",
          os.path.isfile(staged_tex(out)) and os.path.isfile(coop.state_path(stage)))
    m = macros(staged_tex(out)) if os.path.isfile(staged_tex(out)) else {}
    check("C1-4 暂存 .tex 题名 = JSON 值", m.get("mytitle") == "人工题名", str(m.get("mytitle")))
    check("C1-5 暂存 .tex 两位作者 = JSON 值",
          m.get("myfirstauthor") == "\\kaishu 张三"
          and m.get("mysecondauthor") == "\\kaishu 李四",
          f"{m.get('myfirstauthor')!r} / {m.get('mysecondauthor')!r}")
    check("C1-6 暂存 .tex 单位/邮箱 = JSON 值",
          m.get("myfirstaffiliation") == "甲大学" and m.get("myfirstemail") == "z@x.cn",
          f"{m.get('myfirstaffiliation')!r} / {m.get('myfirstemail')!r}")
    check("C1-7 暂存 .tex 关键词 = JSON 值", m.get("mykeywords") == "甲；乙",
          str(m.get("mykeywords")))
    st = read_state(out)
    sm = st.get("structured_metadata") or {}
    want = coop.structured_metadata_sha256(sm.get("values") or {})
    check("C1-8 状态记 structured_metadata（schema/origin/values/sha256）",
          sm.get("schema") == "metadata.v1" and sm.get("origin") == "cli-json"
          and sm.get("sha256") == want and isinstance(sm.get("values"), dict), str(sm)[:200])
    check("C1-9 状态 values 保留 JSON 归一化值（题名/作者数）",
          (sm.get("values") or {}).get("title_cn") == "人工题名"
          and len((sm.get("values") or {}).get("authors") or []) == 2)
    check("C1-10 params 指纹含 structured_metadata_sha256 且与状态一致",
          (st.get("params") or {}).get("structured_metadata_sha256") == want)
    check("C1-11 状态阶段仍 ph1", st.get("phase") == "ph1")
    check("C1-12 日志不再出现『暂不支持』", "暂不支持" not in (r.stdout + r.stderr))


# ── C2：第二段不带 JSON，继承初排人工值并交付 ─────────────────────────────


def test_c2_ph2_inherit(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "c2")
    form = write_json(os.path.join(tmp, "c2", "form.json"), _FORM)
    r1 = run_convert(src, out, ["--bib", bib, "--verify-refs",
                                "--coop-taskbook-only", "--metadata-json", form])
    check("C2-1 ph1 成功", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    os.remove(form)  # 第二段不依赖第一段的临时 JSON
    replies = _reply_dir(tmp, "c2")
    r2 = run_convert(src, out, ["--verify-refs", "--coop-replies", replies])
    check("C2-2 删掉临时 JSON 后 ph2 不带 JSON 退出 0", r2.returncode == 0,
          (r2.stdout + r2.stderr)[-500:])
    final = os.path.join(out, "Meta")
    m = macros(out_tex(out)) if os.path.isfile(out_tex(out)) else {}
    check("C2-3 交付 .tex 保留初排人工题名", m.get("mytitle") == "人工题名",
          str(m.get("mytitle")))
    check("C2-4 交付 .tex 保留初排两位作者",
          m.get("myfirstauthor") == "\\kaishu 张三"
          and m.get("mysecondauthor") == "\\kaishu 李四")
    check("C2-5 交付 .tex 保留初排单位/关键词",
          m.get("myfirstaffiliation") == "甲大学" and m.get("mykeywords") == "甲；乙")
    rec = os.path.join(final, coop.RECORD_NAME)
    check("C2-6 协同记录写明阶段 done 且暂存目录已清理",
          os.path.isfile(rec) and "阶段：`done`" in read(rec)
          and not os.path.exists(os.path.join(out, "Meta_待核")))
    check("C2-7 状态文件不随交付进最终目录",
          not os.path.exists(os.path.join(final, coop.STATE_NAME)))


# ── C3：ph2 显式带 JSON：同值续跑、改值 exit 2 且产物不动 ─────────────────


def test_c3_ph2_json_changed(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "c3")
    form_a = write_json(os.path.join(tmp, "c3", "a.json"), _FORM)
    r1 = run_convert(src, out, ["--bib", bib, "--verify-refs",
                                "--coop-taskbook-only", "--metadata-json", form_a])
    check("C3-1 ph1 成功", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    before = sha(staged_tex(out))
    changed = dict(_FORM)
    changed["title_cn"] = "人工题名改"
    form_b = write_json(os.path.join(tmp, "c3", "b.json"), changed)
    replies = _reply_dir(tmp, "c3")
    r_b = run_convert(src, out, ["--verify-refs", "--coop-replies", replies,
                                 "--metadata-json", form_b])
    allout = r_b.stdout + r_b.stderr
    check("C3-2 ph2 带改过的 JSON → exit 2", r_b.returncode == 2, allout[-400:])
    check("C3-3 报错点名『人工元数据已变更』并指向重跑第一段",
          "人工元数据已变更" in allout and "重跑第一段" in allout)
    check("C3-4 改值失败不交付、暂存 .tex 一个字节不动",
          not os.path.exists(os.path.join(out, "Meta")) and sha(staged_tex(out)) == before)
    r_a = run_convert(src, out, ["--verify-refs", "--coop-replies", replies,
                                 "--metadata-json", form_a])
    m = macros(out_tex(out)) if os.path.isfile(out_tex(out)) else {}
    check("C3-5 同值 JSON 续跑 exit 0 且交付值不变",
          r_a.returncode == 0 and m.get("mytitle") == "人工题名",
          (r_a.stdout + r_a.stderr)[-400:])


# ── C3b：旧状态（无 structured_metadata）兼容 ─────────────────────────────


def test_c3b_old_state(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "c3b")
    r1 = run_convert(src, out, ["--bib", bib, "--verify-refs", "--coop-taskbook-only"])
    check("C3b-1 旧式 ph1（不带 JSON）成功", r1.returncode == 0,
          (r1.stdout + r1.stderr)[-300:])
    form = write_json(os.path.join(tmp, "c3b", "form.json"), _FORM)
    replies = _reply_dir(tmp, "c3b")
    r_bad = run_convert(src, out, ["--verify-refs", "--coop-replies", replies,
                                   "--metadata-json", form])
    allout = r_bad.stdout + r_bad.stderr
    check("C3b-2 旧状态 + ph2 带 JSON → exit 2 并指向重跑第一段",
          r_bad.returncode == 2 and "重跑第一段" in allout
          and not os.path.exists(os.path.join(out, "Meta")), allout[-400:])
    r_ok = run_convert(src, out, ["--verify-refs", "--coop-replies", replies])
    check("C3b-3 旧状态 + ph2 不带 JSON → 照常 exit 0",
          r_ok.returncode == 0 and os.path.isdir(os.path.join(out, "Meta")),
          (r_ok.stdout + r_ok.stderr)[-300:])


# ── C4：B-223 同义通道 + 优先级 + 非两段式与 HEAD 一致 ────────────────────


def test_c4_channels_and_priority(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "c4")
    form = write_json(os.path.join(tmp, "c4", "form.json"), _FORM)
    r = run_convert(src, out, ["--bib", bib, "--verify-refs",
                               "--coop-taskbook-only", "--metadata", form])
    m = macros(staged_tex(out)) if os.path.isfile(staged_tex(out)) else {}
    st = read_state(out)
    sm = st.get("structured_metadata") or {}
    check("C4-1 `--metadata x.json` 同义通道：ph1 exit 0 且写值",
          r.returncode == 0 and m.get("mytitle") == "人工题名"
          and (st.get("phase") == "ph1"), (r.stdout + r.stderr)[-300:])
    check("C4-2 `--metadata x.json` 也记 origin=cli-json + sha256",
          sm.get("origin") == "cli-json"
          and sm.get("sha256") == coop.structured_metadata_sha256(sm.get("values") or {}))

    # 非两段式：JSON > --title-cn（优先级矩阵引擎侧已有，此处防回归）。
    out2 = os.path.join(tmp, "c4", "out2")
    r2 = run_convert(src, out2, ["--bib", bib, "--metadata-json", form,
                                 "--title-cn", "命令行题名"])
    m2 = macros(out_tex(out2)) if os.path.isfile(out_tex(out2)) else {}
    check("C4-3 非两段式优先级 --metadata-json > --title-cn",
          r2.returncode == 0 and m2.get("mytitle") == "人工题名", str(m2.get("mytitle")))

    # 非两段式无 JSON：产物与 HEAD 引擎逐字节相同（默认零变化）。
    head = ensure_head_engine(tmp)
    if not head:
        skip("C4-4", "git archive HEAD 不可用")
        return
    out_cur = os.path.join(tmp, "c4", "out_cur")
    out_head = os.path.join(tmp, "c4", "out_head")
    rc = run_convert(src, out_cur, ["--bib", bib])
    rh = run_convert(src, out_head, ["--bib", bib], engine=head)
    same = (rc.returncode == 0 and rh.returncode == 0
            and os.path.isfile(out_tex(out_cur)) and os.path.isfile(out_tex(out_head))
            and sha(out_tex(out_cur)) == sha(out_tex(out_head)))
    check("C4-4 非两段式无 JSON：.tex 与 HEAD 引擎逐字节相同", same,
          (rc.stdout + rc.stderr)[-200:] + (rh.stdout + rh.stderr)[-200:])


# ── C5：默认零变化（无 JSON 的 ph1 状态 params 与 HEAD 完全一致）──────────


def test_c5_default_zero_change(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "c5")
    # 显式给模板：`params` 的 sty/skeleton 是绝对路径；官方/HEAD 两份引擎解包位置
    # 不同会制造假差异，显式指向本仓同一份模板后可比。
    tpl = os.path.join(_REPO, "编辑笔记", "模板文件")
    tpl_args = ["--sty", os.path.join(tpl, "SLCN-MIX.sty"),
                "--skeleton", os.path.join(tpl, "CN_template.tex")]
    base = ["--bib", bib, "--verify-refs", "--coop-taskbook-only", *tpl_args]
    r = run_convert(src, out, list(base))
    st = read_state(out)
    check("C5-1 无 JSON 时 ph1 成功", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    check("C5-2 无 JSON 时状态不含 structured_metadata 键",
          bool(st) and "structured_metadata" not in st)
    check("C5-3 无 JSON 时 params 不含 structured_metadata_sha256",
          "structured_metadata_sha256" not in (st.get("params") or {}))

    head = ensure_head_engine(tmp)
    if not head:
        skip("C5-4", "git archive HEAD 不可用")
        return
    params_cur = dict(st.get("params") or {})
    fp_cur = st.get("fingerprint")
    rh = run_convert(src, out, list(base), engine=head)
    st_head = read_state(out)  # 同一暂存目录，路径相同，可直接逐键比较
    hp = st_head.get("params") or {}
    diff = {k: (params_cur.get(k), hp.get(k))
            for k in sorted(set(params_cur) | set(hp))
            if params_cur.get(k) != hp.get(k)}
    check("C5-4 无 JSON：params 与 HEAD 逐键相同（含路径/指纹）",
          rh.returncode == 0 and hp == params_cur
          and st_head.get("fingerprint") == fp_cur,
          f"rc={rh.returncode} diff={diff}")


# ── C7：契约/用户文档不得再有反义表述 ─────────────────────────────────────


_DOCS = {
    "Engine/docs/ENGINE_API.md": os.path.join(_ENGINE, "docs", "ENGINE_API.md"),
    "Engine/docs/GUIDE.md": os.path.join(_ENGINE, "docs", "GUIDE.md"),
    "Engine/docs/INVARIANTS.md": os.path.join(_ENGINE, "docs", "INVARIANTS.md"),
    "docs/features/single.md": os.path.join(_REPO, "docs", "features", "single.md"),
    "docs/guide/quickstart.md": os.path.join(_REPO, "docs", "guide", "quickstart.md"),
    "使用说明.md": os.path.join(_REPO, "使用说明.md"),
}


def test_c7_contract_docs() -> None:
    bad = ("暂不支持协同两段式", "不支持人工元数据",
           "人工元数据无法跨进程保留")
    for label, path in _DOCS.items():
        text = read(path) if os.path.isfile(path) else ""
        hit = [w for w in bad if w in text]
        check(f"C7-1 {label} 无反义表述", os.path.isfile(path) and not hit, str(hit))
    api = read(_DOCS["Engine/docs/ENGINE_API.md"])
    check("C7-2 ENGINE_API 记录 structured_metadata 状态键",
          "structured_metadata" in api)
    check("C7-3 ENGINE_API 说明两段式接受 --metadata-json",
          "--metadata-json" in api and "协同" in api)


# ── 运行器 ────────────────────────────────────────────────────────────────


def main() -> int:
    global ENV
    tmp = tempfile.mkdtemp(prefix="b287_")
    notes = os.path.join(tmp, "notes")
    os.makedirs(notes, exist_ok=True)
    ENV = make_env(notes)
    try:
        test_c1_ph1_with_json(tmp)
        test_c2_ph2_inherit(tmp)
        test_c3_ph2_json_changed(tmp)
        test_c3b_old_state(tmp)
        test_c4_channels_and_priority(tmp)
        test_c5_default_zero_change(tmp)
        test_c7_contract_docs()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {PASS} 项 / 失败 {len(FAILS)} 项"
          + (f"；跳过 {len(SKIPS)} 项" if SKIPS else ""))
    if FAILS:
        print("失败：" + "；".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
