#!/usr/bin/env python3
"""B-285a S3 契约测试：四类报告摘要头（结论/计数/top5/折叠明细），旧正文逐行找回。

判据（B-285 spec C5）：四类均含结论+计数+top5；旧正文行按原顺序可逐行找回；
infocheck `.txt` sha256 不变（见 test_b285_contract 的冻结快照）；同输入报告逐字节相同。

四类落点：`_一校核查.md`（proofcheck）、`_回读校验报告.md`（bibreport）、
`信息检查<stamp>_摘要.md`（infocheck）、`编译报告.md`（compileall）。

本文件是独立脚本（与 `Engine/tests/` 其余套件同一风格），失败退出码非 0。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import bibreport as BR  # noqa: E402
from slcn import infocheck as IC  # noqa: E402
from slcn import report_header as RH  # noqa: E402

PY = sys.executable
_passed = 0
_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}  {detail}")


def _load_compileall():
    """按文件路径加载 Engine/compileall.py（避免与标准库同名模块混淆）。"""
    spec = importlib.util.spec_from_file_location("b285_compileall", ENGINE / "compileall.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def check_summary_block(md: str, label: str) -> None:
    """四类报告共用的摘要读法（C5：结论+计数+top5+折叠明细）。

    新增报告（信息检查摘要 / 编译报告）带自己的 `# 标题`，标题后紧跟摘要块；
    就地插入的旧报告（一校核查 / 回读校验）摘要块就是第一段（旧正文随后）。
    """
    head = md
    if not head.startswith("**摘要**："):
        cut = head.find("\n\n**摘要**：")
        head = head[cut + 2:] if cut >= 0 else head
    check(f"{label}：摘要块在最前（或紧跟报告标题）", head.startswith("**摘要**："), md[:40])
    check(f"{label}：含计数行", "**计数**：" in md)
    check(f"{label}：含 top5 标题", "**最需处理（top 5）**" in md)
    check(f"{label}：含折叠明细块", "<details>" in md and "<summary>全部明细（" in md
          and "</details>" in md)
    body = RH.split(md)[1]
    check(f"{label}：旧正文可逐行找回（header 后即旧正文）", bool(body) and md.endswith(body))


# ─────────────────────────────────────────────────────────────────────────
def test_pure_render() -> None:
    print("\n── (a) report_header 纯函数：格式、确定、top5、注入时钟 ──")
    H = RH.render(
        conclusion="有 3 处需要人工处理",
        counts=(("问题", 3), ("提示", 2)),
        top=[f"第 {i} 条" for i in range(7)],
        details=[f"明细 {i}" for i in range(3)],
    )
    check_summary_block(H + "OLD\n", "(a) render")
    check("(a) 计数按给定顺序打印", "**计数**：问题 3 ｜ 提示 2" in H, H[:200])
    numbered = [ln for ln in H.splitlines() if re.match(r"^\d+\. ", ln)]
    check("(a) top5 只取前 5 条", len(numbered) == RH.TOP_LIMIT and numbered[0] == "1. 第 0 条",
          str(numbered))
    check("(a) 明细全列出（不截断条数）", H.count("- 明细 ") == 3)
    check("(a) 默认不出现时间（逐字节确定）", "**生成**" not in H)
    check("(a) 同入参两次逐字节相同", H == RH.render(
        conclusion="有 3 处需要人工处理", counts=(("问题", 3), ("提示", 2)),
        top=[f"第 {i} 条" for i in range(7)], details=[f"明细 {i}" for i in range(3)]))
    Hw = RH.render(conclusion="有失败", when="2026-10-30T00:00:00+0800")
    check("(a) 时钟可注入", "**生成**：2026-10-30T00:00:00+0800" in Hw, Hw[:120])
    Hn = RH.render(conclusion="第一行\n第二行", top=[], details=[])
    check("(a) 结论里的换行被压平", "**摘要**：第一行 第二行" in Hn, Hn[:120])
    check("(a) 无 top/明细时给「（无）」", "（无）" in Hn)

    header = RH.render(conclusion="结论", counts=(("问题", 0),), top=(), details=())
    body = "# 旧正文\n\n旧行 1\n旧行 2\n"
    md = RH.insert(body, header)
    check("(a) insert = header + 旧正文（旧正文逐字节不动）", md == header + body)
    h2, b2 = RH.split(md)
    check("(a) split 可拆回（测试/排查用）", h2 == header and b2 == body)
    long_item = "长" * 300
    Hl = RH.render(conclusion="x", details=[long_item])
    line = [ln for ln in Hl.splitlines() if ln.startswith("- 长")][0]
    check("(a) 单条明细过长会截断（≤160 字符）", len(line) <= 162, str(len(line)))


def test_proofcheck() -> None:
    print("\n── (b) 一校核查 `_一校核查.md`：摘要头 + 旧正文 + 确定 ──")
    tmp = tempfile.mkdtemp(prefix="slcn_rh_proof_")
    try:
        tex = os.path.join(tmp, "Test_Author.tex")
        with open(tex, "w", encoding="utf-8") as fh:
            fh.write("\\documentclass{article}\n\\begin{document}\n"
                     + "这是一段用来触发排版检查的中文测试正文，含半角标点,和引号\"好\"。\n" * 30
                     + "\\end{document}\n")
        md_a = os.path.join(tmp, "a.md")
        md_b = os.path.join(tmp, "b.md")
        r1 = subprocess.run([PY, str(ENGINE / "proofcheck.py"), "--tex", tex, "--out", md_a],
                            cwd=str(ENGINE), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        r2 = subprocess.run([PY, str(ENGINE / "proofcheck.py"), "--tex", tex, "--out", md_b],
                            cwd=str(ENGINE), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        check("(b) 退出码仍是 0/1（有命中=1，不因报告头改变）",
              r1.returncode in (0, 1) and r1.returncode == r2.returncode, f"rc={r1.returncode}")
        check("(b) 默认 stdout 不含摘要头（旧 CLI 输出零变化）",
              "摘要" not in r1.stdout and "top 5" not in r1.stdout)
        md1 = Path(md_a).read_text(encoding="utf-8")
        md2 = Path(md_b).read_text(encoding="utf-8")
        check_summary_block(md1, "(b) 一校核查")
        h1, body = RH.split(md1)
        check("(b) 摘要头与正文分界正确、旧正文首行是原标题",
              body.startswith("# 一校核查报告：Test_Author"), body[:60])
        check("(b) 旧正文原有结构行都在（## 摘要 / 合计 / 逐条）",
              "## 摘要" in body and "**合计命中" in body and "## 逐条" in body)
        check("(b) 同输入两次：摘要头逐字节相同（正文含分钟级时间戳，属旧行为）",
              RH.split(md2)[0] == h1)
        check("(b) 报告落了盘且旧正文没有被删行",
              body.count("\n") >= 8 and md1.endswith("\n"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bibreport() -> None:
    print("\n── (c) 回读校验 `_回读校验报告.md`：摘要头 + 结论单源 + 确定 ──")
    bib = ("@article{zhang2020,\n  title = {示例文献},\n  author = {Zhang, San},\n"
           "  year = {2020},\n}\n")
    md1, concl = BR.build_report_and_conclusion(bib, returned_path="退回.bib")
    md2, concl2 = BR.build_report_and_conclusion(bib, returned_path="退回.bib")
    check("(c) 结论行仍是 conclusion_line 单源", md1.startswith(f"**摘要**：{concl}"),
          md1[:120])
    check("(c) 结论行口径未变（`结论：N 处问题 / M 处提示`）",
          re.match(r"^结论：\d+ 处问题 / \d+ 处提示 —— ", concl) is not None, concl)
    check_summary_block(md1, "(c) 回读校验")
    h1, body = RH.split(md1)
    check("(c) 旧正文首行仍是原标题", body.startswith("# 退回版参考文献核对报告"), body[:60])
    check("(c) 旧正文分节/附录行都在",
          "## 一、可解析性（语法）" in body and "## 附录：逐条状态" in body
          and "⚠️ 本报告只回答" in body)
    check("(c) 同输入两次报告逐字节相同", md1 == md2 and concl == concl2)
    check("(c) 计数行含问题/提示/条目", re.search(r"\*\*计数\*\*：问题 \d+ ｜ 提示 \d+ ｜ 条目 \d+",
                                              md1) is not None)


def _make_issue(root: Path) -> None:
    folder = root / "2026年第5期(90)" / "01 Test_A"
    folder.mkdir(parents=True)
    (folder / "Test_A.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n测试。\n\\end{document}\n", encoding="utf-8")


def test_infocheck() -> None:
    print("\n── (d) 信息检查：旧 txt/json 字段不动 + 新增 `_摘要.md` ──")
    tmp = Path(tempfile.mkdtemp(prefix="slcn_rh_info_"))
    try:
        root_a = tmp / "a"
        root_b = tmp / "b"
        _make_issue(root_a)
        _make_issue(root_b)
        IC.process_folders(str(root_a / "2026年第5期(90)"), out_dir=str(root_a),
                           stamp="STAMP", log=lambda _s: None)
        IC.process_folders(str(root_b / "2026年第5期(90)"), out_dir=str(root_b),
                           stamp="STAMP", log=lambda _s: None)
        txt_a = (root_a / "信息检查STAMP.txt").read_bytes()
        json_a = json.loads((root_a / "信息检查STAMP.json").read_text(encoding="utf-8"))
        summary = (root_a / "信息检查STAMP_摘要.md").read_text(encoding="utf-8")
        txt_b = (root_b / "信息检查STAMP.txt").read_bytes()

        check("(d) 旧 .txt 仍是 CRLF 字节（没有被摘要头改写）",
              b"\r\n" in txt_a and "\ufffd" not in txt_a.decode("utf-8", "replace"))
        check("(d) 旧 .txt 同输入两次逐字节相同", txt_a == txt_b)
        check("(d) 旧 .json 顶层字段原样（generatedAt/issueFolder/articles）",
              {"generatedAt", "issueFolder", "articles"} <= set(json_a), str(set(json_a)))
        check("(d) .json 只新增 summary 一个键",
              set(json_a) - {"generatedAt", "issueFolder", "articles"} == {"summary"},
              str(set(json_a)))
        sum_a = json_a["summary"]
        check("(d) summary 字段给外壳用：articles/hasTeX/hasPDF/inconsistencies/conclusion",
              set(sum_a) == {"articles", "hasTeX", "hasPDF", "inconsistencies", "conclusion"}
              and sum_a["articles"] == 1 and sum_a["hasTeX"] == 1
              and sum_a["inconsistencies"] == 0, str(sum_a))
        check("(d) `_摘要.md` 是新增文件且含完整摘要块", summary.startswith("# 信息检查摘要\n\n"))
        check_summary_block(summary, "(d) 信息检查")
        check("(d) 摘要明细逐篇列出", "`Test_A`" in summary)
        check("(d) 摘要文件同输入两次逐字节相同",
              summary == (root_b / "信息检查STAMP_摘要.md").read_text(encoding="utf-8"))

        # 反例：不一致项进 top5（用纯函数夹具，不依赖真 PDF）
        bad = IC._summary_md([], ["01 X：声明页数 8，实际 7 页"])
        check("(d) 不一致时结论/计数/top5 都说真话",
              "1 项不一致" in bad and "**计数**：篇数 0" in bad
              and "01 X：声明页数 8，实际 7 页" in bad, bad[:200])

        root_c = tmp / "c"
        _make_issue(root_c)
        IC.process_folders(str(root_c / "2026年第5期(90)"), out_dir=str(root_c),
                           stamp="PREVIEW", log=lambda _s: None, write=False)
        check("(d) --preview（write=False）不写任何文件",
              sorted(p.name for p in root_c.iterdir()) == ["2026年第5期(90)"],
              str(sorted(p.name for p in root_c.iterdir())))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compileall() -> None:
    print("\n── (e) 编译报告 `编译报告.md`：结论+计数+首条 ! 行+.log 路径 ──")
    mod = _load_compileall()
    tmp = Path(tempfile.mkdtemp(prefix="slcn_rh_compile_"))
    try:
        (tmp / "01 A").mkdir()
        (tmp / "01 A" / "A.log").write_text(
            "This is XeTeX\n! Undefined control sequence.\nl.12 \\foo\n", encoding="utf-8")
        failed = [("A", str(tmp / "01 A"), "编译失败"),
                  ("B", str(tmp / "02 B"), "找不到命令 xelatex，无法编译")]
        mod._write_compile_report(str(tmp), 2, 0, 2, failed)
        md = (tmp / "编译报告.md").read_text(encoding="utf-8")
        check("(e) 标题正确", md.startswith("# 编译报告\n\n"), md[:40])
        check_summary_block(md, "(e) 编译报告")
        check("(e) 结论/计数说清成功失败", "2 篇编译失败" in md and "**计数**：篇数 2 ｜ 成功 0 ｜ 失败 2" in md)
        check("(e) 失败首条 `!` 行进明细", "! Undefined control sequence." in md)
        check("(e) 明细带 `.log` 路径", "A.log" in md and "（日志：`" in md)
        check("(e) 没有 .log 的失败回退到错误信息", "找不到命令 xelatex" in md)
        md1 = md
        mod._write_compile_report(str(tmp), 2, 0, 2, failed)
        check("(e) 同输入两次逐字节相同",
              md1 == (tmp / "编译报告.md").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("B-285a 报告摘要头契约测试（四类报告）")
    test_pure_render()
    test_proofcheck()
    test_bibreport()
    test_infocheck()
    test_compileall()
    print("\n" + "=" * 64)
    if _fails:
        print(f"失败 {len(_fails)} 项（共 {_passed + len(_fails)} 项）：" + "；".join(_fails[:8]))
        return 1
    print(f"全部 {_passed} 项断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
