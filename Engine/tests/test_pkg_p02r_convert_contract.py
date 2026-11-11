#!/usr/bin/env python3
r"""P02R（P02-R13）：转换产物契约回归 —— R01/R02/R03 的正反例。

本文件随 P02-R13（批次外 FIX，P13 3.13 退回 P02）新增，逐条覆盖三个缺陷：
  · R01 / D-P13-02：无参考文献的 Word 稿，生成的 tex 不得声明不存在的
    `reference.bib`（否则 `cleantemp --mode daily` 预检 rc2「引用缺失」，整条清理链被卡死）；
    同用例清理链必须 rc0。有参考文献的稿子仍照常声明并产出 reference.bib（防误删）。
    LaTeX 通道同判据：源内手写 `\bibitem`（无独立 .bib）会自动生成 reference.bib，
    此时声明必须保留；无 bib 且无 `\bibitem` 时不得留悬空声明。
  · R02 / D-P13-03：同 stem 重跑省略 --compile 且 tex 已变化时，不得 rc0 交付旧 PDF；
    必须 rc2 + 中文提示（指出加 --compile），且写盘前停止（tex/PDF 逐字节不变）。
    同源重跑（tex 无变化）仍 rc0，不误拦。
  · R03 / D-P13-05：同 stem 换源（源 docx 或显式 --bib 指纹不同）不得静默覆盖；
    必须 rc2 + 中文提示（换 --name 或加 --force 明确确认），既有交付目录逐字节不变。
    旧交付目录没有指纹 → 兼容 warn-only（旧流程不破坏，成功后补记指纹）。

纪律：全部离线——不联网、不调付费 API、不读真实稿件库/真实 notes 库/真实设置、
不跑 TeX（R02 用「伪造旧 PDF」构造场景，判据是 rc2 + 中文提示 + 零写）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_pkg_p02r_convert_contract.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_PUBLIC = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_PUBLIC, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from docx_fixture_gen import make_docx  # noqa: E402

_CONVERT = os.path.join(_ENGINE, "convert.py")
_CLEANTEMP = os.path.join(_ENGINE, "cleantemp.py")

FAILS: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  {detail}" if not cond and detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def _env(tmp: str) -> dict[str, str]:
    """隔离环境：不联网、不读真实稿件库/notes/设置，启动日志写临时目录。"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SLCN_LLM_") and not k.startswith("SLCN_SEARCH_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    env["SLCN_MANUSCRIPTS"] = os.path.join(tmp, "NO_MANUSCRIPTS")
    env["SLCN_NOTES"] = os.path.join(tmp, "NO_NOTES")
    env["P2_SETTINGS"] = os.path.join(tmp, "settings.json")
    env["SLCN_STARTUP_LOG"] = os.path.join(tmp, "startup.log")
    return env


def _run(tmp: str, *args: str, script: str = _CONVERT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, script, *args], cwd=_ENGINE, env=_env(tmp),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _src(dirpath: str, name: str, paras: list[str]) -> str:
    """在独立目录里造 docx（每稿一目录：refs_auto 扫同目录附件，混放会互相污染）。"""
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, name)
    make_docx(path, paras=paras)
    return path


def _first_tex(dirpath: str) -> str:
    """产物目录里唯一的 .tex（作者拼音目录名不写死，按实际产物取）。"""
    if os.path.isdir(dirpath):
        for fn in sorted(os.listdir(dirpath)):
            if fn.endswith(".tex"):
                return os.path.join(dirpath, fn)
    return os.path.join(dirpath, "MISSING.tex")


def _first_dir(root: str) -> str:
    """产物根目录下唯一的产品目录（作者拼音目录）。"""
    if not os.path.isdir(root):
        return root
    dirs = sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)))
    return os.path.join(root, dirs[0]) if dirs else root


def _tree(dirpath: str) -> dict[str, str]:
    """目录内全部文件的 {相对路径: sha256}，用于「拒绝时逐字节零覆盖」断言。"""
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(dirpath):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            out[os.path.relpath(p, dirpath)] = _sha256(p)
    return out


def _missing_bib_targets(tex_text: str, dirpath: str) -> list[str]:
    """tex 中所有 `\\addbibresource`/`\\bibliography` 声明的目标，哪些在产物里不存在。"""
    import re as _re
    targets: list[str] = []
    for m in _re.finditer(r"\\addbibresource(?:\[[^\]]*\])?\{([^}]*)\}", tex_text):
        targets.append(m.group(1).strip())
    for m in _re.finditer(r"\\bibliography\{([^}]*)\}", tex_text):
        targets.extend(t.strip() for t in m.group(1).split(",") if t.strip())
    missing = []
    for t in targets:
        name = t if t.endswith(".bib") else t + ".bib"
        if not os.path.isfile(os.path.join(dirpath, name)):
            missing.append(t)
    return missing


def _tail(p: subprocess.CompletedProcess, n: int = 700) -> str:
    return ((p.stdout or "")[-n:] + "\n" + (p.stderr or "")[-n:]).strip()


def _paras_no_refs(title: str, author: str = "张三") -> list[str]:
    return [title, author, "摘要：这是测试摘要。", "关键词：逻辑；测试",
            "正文第一段，讨论一个逻辑问题，没有任何引用。",
            "正文第二段，仍然没有引用。"]


def _paras_with_refs(title: str, author: str = "李四") -> list[str]:
    return [title, author, "摘要：这是测试摘要。", "关键词：测试",
            "正文引用了张三（2020）的观点。",
            "参考文献",
            "张三, 2020, “测试文献题名”, 逻辑学研究, 1(1): 1-10."]


# ── R01（D-P13-02）：bib 声明必须与产物一致 ───────────────────────────────


def test_r01_bib_declaration(tmp: str) -> None:
    base = os.path.join(tmp, "R01")
    out = os.path.join(base, "out")
    src = _src(os.path.join(base, "srcA"), "无参考文献_甲.docx",
               _paras_no_refs("无参考文献测试稿"))
    p = _run(tmp, "--source", src, "--out", out)
    check("R01-1 无参考文献稿转换成功（rc0）", p.returncode == 0, _tail(p))
    final = _first_dir(out)
    tex = _first_tex(final)
    text = _read(tex) if os.path.isfile(tex) else ""
    check("R01-2 产物 tex 存在", os.path.isfile(tex), final)
    check("R01-3 tex 不再声明不存在的 reference.bib",
          "\\addbibresource{reference.bib}" not in text)
    check("R01-4 产物目录里确实没有 reference.bib",
          not os.path.exists(os.path.join(final, "reference.bib")))
    check("R01-5 日志说明已丢弃悬空声明（可诊断）",
          "已移除 tex 中指向不存在的 reference.bib" in p.stdout, _tail(p))

    ct = _run(tmp, "--folder", final, "--mode", "daily", script=_CLEANTEMP)
    check("R01-6 同用例 cleantemp --mode daily 通过（rc0）", ct.returncode == 0, _tail(ct))
    check("R01-7 日常清理后 .tex 与 original/ 仍保留",
          os.path.isfile(tex) and os.path.isdir(os.path.join(final, "original")))

    # 反例（防误删）：有参考文献的稿子仍必须声明并产出 reference.bib。
    out2 = os.path.join(base, "out2")
    src2 = _src(os.path.join(base, "srcB"), "有参考文献_丙.docx",
                _paras_with_refs("有参考文献测试稿"))
    q = _run(tmp, "--source", src2, "--out", out2)
    check("R01-8 有参考文献稿转换成功（rc0）", q.returncode == 0, _tail(q))
    final2 = _first_dir(out2)
    tex2 = _first_tex(final2)
    text2 = _read(tex2) if os.path.isfile(tex2) else ""
    check("R01-9 有参考文献稿仍声明 reference.bib 且 bib 文件存在",
          "\\addbibresource{reference.bib}" in text2
          and os.path.isfile(os.path.join(final2, "reference.bib")))
    ct2 = _run(tmp, "--folder", final2, "--mode", "daily", script=_CLEANTEMP)
    check("R01-10 有参考文献稿的日常清理也 rc0", ct2.returncode == 0, _tail(ct2))

    # LaTeX 通道（内部验收抓到的反例）：源稿手写 \bibitem、无独立 .bib 时，
    # 产物会自动生成 reference.bib —— 该场景声明必须保留，否则 PDF 静默丢书目。
    out3 = os.path.join(base, "out3")
    src3 = os.path.join(base, "srcC", "文献表测试稿.tex")
    os.makedirs(os.path.dirname(src3), exist_ok=True)
    with open(src3, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\\documentclass{article}\n\\begin{document}\n"
                 "正文引用\\cite{li2020}。\n"
                 "\\begin{thebibliography}{9}\n"
                 "\\bibitem{li2020} 李四, 2020, “测试文献题名”, 逻辑学研究, 1(1): 1-10.\n"
                 "\\end{thebibliography}\n\\end{document}\n")
    r3 = _run(tmp, "--source", src3, "--out", out3, "--author", "李四")
    check("R01-11 LaTeX 稿（源内 \\bibitem）转换成功（rc0）", r3.returncode == 0, _tail(r3))
    final3 = _first_dir(out3)
    tex3 = _first_tex(final3)
    text3 = _read(tex3) if os.path.isfile(tex3) else ""
    check("R01-12 \\bibitem 自动转换仍产出 reference.bib",
          os.path.isfile(os.path.join(final3, "reference.bib")))
    check("R01-13 自动生成 reference.bib 时 tex 保留声明（不得丢书目）",
          "\\addbibresource{reference.bib}" in text3)
    check("R01-14 LaTeX 产物声明目标都存在（一致性不变量）",
          r3.returncode == 0 and not _missing_bib_targets(text3, final3),
          str(_missing_bib_targets(text3, final3)))

    # 反例（另一侧）：LaTeX 稿无 bib、无 \bibitem → 不得留悬空声明。
    out4 = os.path.join(base, "out4")
    src4 = os.path.join(base, "srcD", "无文献表测试稿.tex")
    os.makedirs(os.path.dirname(src4), exist_ok=True)
    with open(src4, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\\documentclass{article}\n\\begin{document}\n纯正文，无引用。\n\\end{document}\n")
    r4 = _run(tmp, "--source", src4, "--out", out4, "--author", "王五")
    final4 = _first_dir(out4)
    tex4 = _first_tex(final4)
    text4 = _read(tex4) if os.path.isfile(tex4) else ""
    check("R01-15 LaTeX 稿无 bib 时声明与产物一致（无悬空声明）",
          r4.returncode == 0 and not _missing_bib_targets(text4, final4),
          _tail(r4) + " " + str(_missing_bib_targets(text4, final4)))


# ── R02（D-P13-03）：旧 PDF 不得冒充新稿 ──────────────────────────────────


def test_r02_stale_pdf(tmp: str) -> None:
    base = os.path.join(tmp, "R02")
    out = os.path.join(base, "out")
    src = _src(os.path.join(base, "src"), "二次转换_甲.docx",
               _paras_no_refs("二次转换测试稿"))

    p1 = _run(tmp, "--source", src, "--out", out, "--title-cn", "初版题名")
    check("R02-1 第一轮转换（无 --compile）rc0", p1.returncode == 0, _tail(p1))
    final = _first_dir(out)
    tex = _first_tex(final)
    pdf = os.path.splitext(tex)[0] + ".pdf"
    sha1 = _sha256(tex) if os.path.isfile(tex) else ""
    check("R02-2 第一轮产物 tex 存在且尚无 PDF",
          os.path.isfile(tex) and not os.path.exists(pdf))

    # 构造「上一轮已编译」的旧 PDF（测试不跑 TeX：判据是拒绝，不是重编）。
    fake = b"%PDF-1.4\n% stale pdf fixture\n%%EOF\n"
    with open(pdf, "wb") as f:
        f.write(fake)
    sha1 = _sha256(tex)

    p2 = _run(tmp, "--source", src, "--out", out, "--title-cn", "改版题名")
    check("R02-3 改标题后省略 --compile：拒绝（rc2）", p2.returncode == 2, _tail(p2))
    check("R02-4 中文提示说清下一步（补 --compile）",
          "tex 已更新但 PDF 未更新" in p2.stderr and "--compile" in p2.stderr)
    check("R02-5 拒绝时 stdout 不打印完成契约行",
          "完成。产物目录：" not in p2.stdout)
    with open(pdf, "rb") as f:
        fake_after = f.read()
    check("R02-6 拒绝发生在写盘前：tex 与旧 PDF 逐字节不变",
          _sha256(tex) == sha1 and fake_after == fake)
    check("R02-7 旧 PDF 仍在但没被当成新稿交付（未改名/未删）",
          os.path.isfile(pdf))

    # 同源重跑（tex 无实质变化）不得误拦：旧 PDF 仍同源，rc0 合法。
    p3 = _run(tmp, "--source", src, "--out", out, "--title-cn", "初版题名")
    check("R02-8 同源重跑（tex 不变）仍 rc0，不误拦", p3.returncode == 0, _tail(p3))
    check("R02-9 同源重跑后 tex/PDF 均未被改写",
          _sha256(tex) == sha1 and open(pdf, "rb").read() == fake)


# ── R03（D-P13-05）：同 stem 换源不得静默覆盖 ─────────────────────────────


def test_r03_same_stem(tmp: str) -> None:
    base = os.path.join(tmp, "R03")
    out = os.path.join(base, "out")
    src_a = _src(os.path.join(base, "srcA"), "同stem_甲.docx",
                 _paras_no_refs("同stem甲稿"))
    src_b = _src(os.path.join(base, "srcB"), "同stem_乙.docx",
                 _paras_no_refs("同stem乙稿（不同内容）"))

    pa = _run(tmp, "--source", src_a, "--out", out)
    check("R03-1 首次转换 rc0", pa.returncode == 0, _tail(pa))
    final = _first_dir(out)
    fp_path = os.path.join(final, ".slcn-源指纹.json")
    check("R03-2 交付成功后记录输入源指纹", os.path.isfile(fp_path))
    fp = {}
    try:
        fp = json.loads(_read(fp_path))
    except Exception:  # noqa: BLE001
        pass
    check("R03-3 指纹 schema/源稿 sha256 正确",
          fp.get("schema") == 2  # P02H R01 契约同步：指纹 schema 1 → 2
          and (fp.get("source") or {}).get("sha256") == _sha256(src_a)
          and fp.get("stem") == "San_Zhang", str(fp)[:300])

    pa2 = _run(tmp, "--source", src_a, "--out", out)
    check("R03-4 同源重跑 rc0（不误拦）", pa2.returncode == 0, _tail(pa2))

    before = _tree(final)
    pb = _run(tmp, "--source", src_b, "--out", out)
    check("R03-5 同 stem 不同源：拒绝（rc2）", pb.returncode == 2, _tail(pb))
    check("R03-6 中文提示指出换 --name / 加 --force",
          "拒绝覆盖" in pb.stderr and "--name" in pb.stderr and "--force" in pb.stderr)
    check("R03-7 拒绝时 stdout 不打印完成契约行",
          "完成。产物目录：" not in pb.stdout)
    check("R03-8 拒绝时既有交付目录逐字节不变（零覆盖）", _tree(final) == before)

    pf = _run(tmp, "--source", src_b, "--out", out, "--force")
    check("R03-9 --force 视为显式确认：覆盖成功（rc0）", pf.returncode == 0, _tail(pf))
    fp2 = {}
    try:
        fp2 = json.loads(_read(fp_path))
    except Exception:  # noqa: BLE001
        pass
    check("R03-10 --force 后指纹更新为本次源稿",
          (fp2.get("source") or {}).get("sha256") == _sha256(src_b))

    # 旧交付目录没有指纹：兼容 warn-only，旧流程不破坏，成功后补记指纹。
    out2 = os.path.join(base, "out2")
    qa = _run(tmp, "--source", src_a, "--out", out2)
    final2 = _first_dir(out2)
    fp2_path = os.path.join(final2, ".slcn-源指纹.json")
    check("R03-11 旧版兼容：先有交付再删指纹", qa.returncode == 0
          and os.path.isfile(fp2_path), _tail(qa))
    os.remove(fp2_path)
    qb = _run(tmp, "--source", src_b, "--out", out2)
    check("R03-12 无指纹的旧交付：warn-only（rc0，不阻断旧流程）",
          qb.returncode == 0 and "没有源指纹记录" in qb.stdout, _tail(qb))
    legacy_fp = {}
    try:
        legacy_fp = json.loads(_read(fp2_path))
    except Exception:  # noqa: BLE001
        pass
    check("R03-13 兼容路径成功后补记指纹（指向本次源稿）",
          (legacy_fp.get("source") or {}).get("sha256") == _sha256(src_b))

    # 显式 --bib 也进指纹：bib 换了同样不得静默覆盖。
    out3 = os.path.join(base, "out3")
    src_c_dir = os.path.join(base, "srcC")
    src_c = _src(src_c_dir, "有参考文献_丁.docx",
                 _paras_with_refs("bib指纹测试稿", "王五"))
    bib1 = os.path.join(src_c_dir, "refs1.bib")
    bib2 = os.path.join(src_c_dir, "refs2.bib")
    with open(bib1, "w", encoding="utf-8", newline="\n") as f:
        f.write("@article{k1,\n  author = {张三},\n  title = {甲},\n  year = {2020},\n}\n")
    with open(bib2, "w", encoding="utf-8", newline="\n") as f:
        f.write("@article{k2,\n  author = {李四},\n  title = {乙},\n  year = {2021},\n}\n")
    r1 = _run(tmp, "--source", src_c, "--out", out3, "--bib", bib1)
    check("R03-14 带显式 --bib 首次转换 rc0", r1.returncode == 0, _tail(r1))
    r2 = _run(tmp, "--source", src_c, "--out", out3, "--bib", bib2)
    check("R03-15 同 stem 换 --bib：拒绝（rc2 + 提示参考文献库不同）",
          r2.returncode == 2 and "参考文献库" in r2.stderr, _tail(r2))


# ── main ─────────────────────────────────────────────────────────────────


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="slcn_p02r_") as tmp:
        test_r01_bib_declaration(tmp)
        test_r02_stale_pdf(tmp)
        test_r03_same_stem(tmp)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  FAIL:", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
