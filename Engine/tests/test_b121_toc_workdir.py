#!/usr/bin/env python3
r"""批 121 车道 E / B-357 回归：生成目录中间件落点 + 清理类目 + 英文栏目。

判据（对应契约 `specs/b121-toc-input-temp-cleanup.md` §2 B）：
  (a) B1：时间戳中间 tex 落在 `<期目录>/.toolbox-tmp/`，期目录根不再出现
      `Chinese Content_<stamp>.tex` / `contents_<stamp>.tex`；结果 JSON 的 outputs
      仍是**真实存在的绝对路径**；`--place` 交付固定名不受影响。
  (b) B-375 英文栏目：四项齐全（种类/中文名/英文名/主持人）时英文目录生成
      Special Issue: <columnNameEN>（专辑）/ Column: <columnNameEN>（专栏），
      只用英文名、绝不回退中文名；缺项一律拒绝生成。目录永远列出当前报告全部篇目
      （勾选/专辑只决定栏目标记，不删条目）：带栏目标记的篇目标题和作者都带 ~~~~ 前缀，
      多行标题/作者续行保持同一栏目缩进；普通英文条目两者都不加栏目缩进；
      继承模板没有栏目块时按内置形态生成并用默认间距 3mm（留日志）。
  (c) B2/B3：`cleantemp.py --mode tocwork` 默认移入 `.toolbox-trash`（可 restore），
      清理 `.toolbox-tmp/`、根时间戳 tex、`目录结果.json`、旧 `信息检查*`；默认保留
      最新一组报告，`--all-reports` 才全清；最终交付文件与 `目录设置.json` 永不删。
  (d) B4：`--json` 的 mode/count/entryCount/entries/trashDir 与人类日志同源。

本文件是独立脚本（与 Engine/tests 其余套件同一风格），失败退出码非 0。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import toc as TOC  # noqa: E402

_passed = 0
_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS {name}")
    else:
        _fails.append(name)
        print(f"FAIL {name}  {detail}")


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(ENGINE))


def json_tail(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in (proc.stdout or "").strip().split("\n") if ln.strip()]
    if not lines:
        return {}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {}


REPORT = """\
Qiu_Dejun
2026年第5期
起始页码：1  终止页码：10  页数：10
字号 10.5pt
第一作者中文: \\kaishu 邱德钧
第一作者英文: Qiu Dejun
中文标题: 可知性悖论、形式可证与认知实现
英文标题: Knowability Paradox, Formal Provability and Cognitive Realization
全文行距: 13

Wu_Hao
2026年第5期
起始页码：11  终止页码：20  页数：10
字号 11pt
第一作者中文: \\kaishu 吴昊
第一作者英文: Wu Hao
中文标题: 论雅辛之谜及其解决方案——从扩展的 RZH 标准看
英文标题: On the Yablo Paradox and Its Solutions
全文行距: 13
"""


def make_issue(prefix: str) -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    issue = root / "2026年第5期(90)"
    issue.mkdir()
    report = issue / "信息检查TEST.txt"
    report.write_text(REPORT, encoding="utf-8")
    return issue, report


# ── (a) B1：中间 tex 落隐藏工作目录 ─────────────────────────────────────
print("\n── (a) B1：时间戳中间 tex 落 .toolbox-tmp，根下不再散落 ──")
_issue, _report = make_issue("slcn_b121_work_")
_result = _issue / ".toolbox-tmp" / "目录结果.json"
_proc = run([str(ENGINE / "toc.py"), "--report", str(_report),
             "--result-json", str(_result)])
check("(a1) toc.py rc==0", _proc.returncode == 0,
      f"rc={_proc.returncode} stderr={_proc.stderr.strip()[-300:]}")
_payload = json.loads(_result.read_text(encoding="utf-8"))
_outputs = _payload.get("outputs") or {}
_work = _issue / ".toolbox-tmp"
check("(a2) 根下无时间戳 tex",
      not list(_issue.glob("Chinese Content_*.tex")) and not list(_issue.glob("contents_*.tex")),
      repr(sorted(p.name for p in _issue.iterdir())))
for _key, _stem in (("zhTex", "Chinese Content_"), ("enTex", "contents_")):
    _value = str(_outputs.get(_key) or "")
    _p = Path(_value)
    check(f"(a3) {_key} 在 .toolbox-tmp 下", _p.parent == _work, _value)
    check(f"(a4) {_key} 真实存在（绝对路径）",
          _p.is_absolute() and _p.is_file() and _p.name.startswith(_stem), _value)
check("(a5) 结果 JSON 本身在 .toolbox-tmp 下", _result.is_file(), str(_result))

_proc = run([str(ENGINE / "toc.py"), "--report", str(_report), "--place",
             "--result-json", str(_issue / ".toolbox-tmp" / "目录结果.json")])
check("(a6) --place rc==0", _proc.returncode == 0,
      f"rc={_proc.returncode} stderr={_proc.stderr.strip()[-300:]}")
_payload = json.loads((_issue / ".toolbox-tmp" / "目录结果.json").read_text(encoding="utf-8"))
_toc_dir = Path(str(_payload.get("tocDir") or ""))
check("(a7) 落位固定名 zh", (_toc_dir / "Chinese Content.tex").is_file(), str(_toc_dir))
check("(a8) 落位固定名 en", (_toc_dir / "contents.tex").is_file(), str(_toc_dir))
_placed = _payload.get("placed") or {}
check("(a9) placed 指向 000 固定名",
      str(_placed.get("zhTex", "")).endswith("Chinese Content.tex")
      and str(_placed.get("enTex", "")).endswith("contents.tex"), repr(_placed))

# ── (b) 英文栏目：EN 名空回退中文名 + ~~~~ + 无栏目块告警 ────────────────
print("\n── (b) B-375 英文栏目：Special Issue 行 / ~~~~ / 无 columnBlock 告警 ──")
_issue2, _report2 = make_issue("slcn_b121_en_")
_work2 = _issue2 / ".toolbox-tmp"
_logs: list[str] = []
_settings = TOC.TocSettings(columnKind="专辑", columnName="偏好逻辑",
                            columnNameEN="Preference Logic",
                            columnHost="刘奋荣", columnArticles=["Qiu_Dejun"])
_out = TOC.generate_tex(str(_report2), "en", _settings, log=_logs.append, stamp="B121E")
check("(b1) 英文中间 tex rc/存在", bool(_out) and Path(_out).is_file(), str(_out))
check("(b2) 英文中间 tex 在 .toolbox-tmp", Path(_out or "").parent == _work2, str(_out))
_en = Path(_out or "").read_text(encoding="utf-8") if _out else ""
check("(b3) 专辑英文表头 ⇒ Special Issue: <columnNameEN>（英文名必填、不回退中文名）",
      "Special Issue: Preference Logic" in _en and "偏好逻辑" not in _en, _en[:200])
# B-375：专辑 = 全部当前报告稿件都带栏目标记；columnArticles 只存了 Qiu_Dejun 也不删
# Wu_Hao —— 目录仍列出两篇、两篇标题和作者都带 ~~~
check("(b4) 专辑：两篇都在目录里、标题和作者都带 ~~~~ 前缀",
      "{\\large ~~~~ Knowability" in _en
      and "\\textit{~~~~ Qiu Dejun}" in _en
      and "{\\large ~~~~ On the Yablo" in _en
      and "\\textit{~~~~ Wu Hao}" in _en
      and "Knowability" in _en and "On the Yablo Paradox" in _en,
      repr([ln for ln in _en.split("\n") if "~~~~" in ln][:4]))
check("(b5) 英文栏目列定义是 CANONICAL_EN_COLUMN，且恰好两篇标题/两篇作者带 ~~~~",
      "{@{}>{\\raggedright\\arraybackslash}X@{\\hspace{1em}}r@{}}" in _en
      and sum(1 for ln in _en.split("\n") if ln.startswith("{\\large ~~~~ ")) == 2
      and sum(1 for ln in _en.split("\n") if ln.startswith("\\textit{~~~~ ")) == 2,
      _en.split("\n")[0:6])
check("(b5a) 英文栏目表带 0.99 行高",
      "\\renewcommand{\\arraystretch}{0.99}" in _en, _en.split("\n")[0:8])
check("(b6) 不再有英文名回退日志（B-375：缺项直接拒绝生成）",
      not any("回退" in ln and "英文名" in ln for ln in _logs), repr(_logs))
check("(b7) 内置模板无栏目块告警路径可复现（见 b8/b9）", True)

# 继承模板「有 head 但无栏目块」：应告警并按内置形态补 Column 行
_issue3, _report3 = make_issue("slcn_b121_en2_")
_toc_dir3 = _issue3 / "000 Table of Contents"
_toc_dir3.mkdir()
_toc_dir3.joinpath("contents.tex").write_text(
    "\\documentclass[fontset=windows]{ctexart}\n"
    "\\usepackage{tabularx}\n"
    "\\begin{document}\n"
    "\\begin{tabularx}{\\textwidth}{@{}>{\\raggedright\\arraybackslash}X@{\\hspace{2em}}r@{}}\n"
    "\\large Sample Title\\\\\n"
    "\\textit{{Author}}\\\\\n"
    "& 1\\\\\n"
    "\\end{tabularx}\n"
    "\\end{document}\n", encoding="utf-8")
_logs2: list[str] = []
_out2 = TOC.generate_tex(str(_report3), "en", _settings, log=_logs2.append, stamp="B121E2")
_en2 = Path(_out2 or "").read_text(encoding="utf-8") if _out2 else ""
check("(b8) 继承模板无栏目块 ⇒ 仍生成 Special Issue 行",
      "Special Issue: Preference Logic" in _en2, repr(_en2[:200]))
check("(b9) 继承模板无栏目块 ⇒ 明确告警/说明",
      any("没有栏目块" in ln for ln in _logs2), repr(_logs2))
_en2_lines = _en2.split("\n")
check("(b10) 无 columnBlock ⇒ Column 行后默认间距 3mm",
      any(ln.strip().endswith("\\[3mm]") for ln in _en2_lines),
      repr([ln for ln in _en2_lines if "3mm" in ln and "geometry" not in ln]))

# ── (c) B2/B3：清理类目（trash 可恢复、最新报告保留、最终文件不动）─────
print("\n── (c) B2/B3：--mode tocwork 清理类目 ──")
_issue4 = Path(tempfile.mkdtemp(prefix="slcn_b121_clean_")) / "2026年第5期(90)"
_issue4.mkdir()
(_issue4 / "000 Table of Contents").mkdir()
_final_zh = _issue4 / "000 Table of Contents" / "Chinese Content.tex"
_final_en = _issue4 / "000 Table of Contents" / "contents.tex"
_final_pdf = _issue4 / "Chinese Content.pdf"
_settings_json = _issue4 / "目录设置.json"
for _p in (_final_zh, _final_en, _final_pdf, _settings_json):
    _p.write_text("FINAL", encoding="utf-8")
_root_tex = [_issue4 / "Chinese Content_2026-11-04_23-00-00.tex",
             _issue4 / "contents_2026-11-04_23-00-00.tex"]
for _p in _root_tex:
    _p.write_text("MID", encoding="utf-8")
_result4 = _issue4 / "目录结果.json"
_result4.write_text("{}", encoding="utf-8")
_work4 = _issue4 / ".toolbox-tmp"
_work4.mkdir()
for _name in ("Chinese Content_2026-11-04_23-30-00.tex", "contents_2026-11-04_23-30-00.tex",
              "stamp.txt"):
    (_work4 / _name).write_text("TMP", encoding="utf-8")
_old = [_issue4 / f"信息检查2026_01_01_00_00_00.{_ext}" for _ext in ("txt", "json")]
_old.append(_issue4 / "信息检查2026_01_01_00_00_00_摘要.md")
_new = [_issue4 / f"信息检查2026_11_04_22_10_40.{_ext}" for _ext in ("txt", "json")]
_new.append(_issue4 / "信息检查2026_11_04_22_10_40_摘要.md")
for _p in _old + _new:
    _p.write_text("REPORT", encoding="utf-8")

_dry = run([str(ENGINE / "cleantemp.py"), "--folder", str(_issue4),
            "--mode", "tocwork", "--dry-run", "--json"])
_pd = json_tail(_dry)
check("(c1) dry-run rc==0", _dry.returncode == 0,
      f"rc={_dry.returncode} stderr={_dry.stderr.strip()[-200:]}")
check("(c2) dry-run JSON schema",
      _pd.get("ok") is True and _pd.get("mode") == "tocwork"
      and _pd.get("dryRun") is True and isinstance(_pd.get("entries"), list),
      repr(_pd)[:300])
check("(c3) dry-run 计数=9（3 tmp + 2 根 tex + 1 结果 + 3 旧报告）",
      _pd.get("count") == 9 and _pd.get("entryCount") == 9, repr(_pd.get("count")))
_dry_paths = {e.get("path") for e in (_pd.get("entries") or [])}
check("(c4) 预演列出新类目", {"目录结果.json", ".toolbox-tmp/stamp.txt"} <= _dry_paths,
      repr(sorted(_dry_paths)))
check("(c5) 预演不列最新一组报告",
      not any("2026_11_04_22_10_40" in p for p in _dry_paths), repr(sorted(_dry_paths)))
check("(c6) 预演不列最终交付/设置",
      not (_dry_paths & {"Chinese Content.tex", "contents.tex", "目录设置.json"}), repr(sorted(_dry_paths)))
check("(c7) dry-run 未真动文件", _root_tex[0].is_file() and _old[0].is_file(), "")

_real = run([str(ENGINE / "cleantemp.py"), "--folder", str(_issue4),
             "--mode", "tocwork", "--json"])
_pr = json_tail(_real)
_trash = str(_pr.get("trashDir") or "")
check("(c8) 实际清理 rc==0", _real.returncode == 0,
      f"rc={_real.returncode} stderr={_real.stderr.strip()[-300:]}")
check("(c9) 实际清理计数=9", _pr.get("count") == 9, repr(_pr.get("count")))
check("(c10) 回收目录存在且 manifest 有 9 条",
      bool(_trash) and (Path(_trash) / "manifest.json").is_file()
      and len(json.loads((Path(_trash) / "manifest.json").read_text(encoding="utf-8"))["entries"]) == 9,
      _trash)
check("(c11) 根时间戳 tex 已移走", not _root_tex[0].exists() and not _root_tex[1].exists())
check("(c12) 目录结果.json 已移走", not _result4.exists())
check("(c13) .toolbox-tmp 内容已清空", not _work4.exists() or not list(_work4.iterdir()),
      repr(sorted(_work4.iterdir()) if _work4.exists() else "gone"))
check("(c14) 旧报告已移走、最新一组保留",
      not _old[0].exists() and _new[0].is_file() and _new[1].is_file()
      and _new[2].is_file())
check("(c15) 最终交付/设置原样保留",
      _final_zh.is_file() and _final_en.is_file() and _final_pdf.is_file()
      and _settings_json.is_file())

_restore = run([str(ENGINE / "cleantemp.py"), "--folder", str(_issue4),
                "--restore", _trash, "--json"])
_pe = json_tail(_restore)
check("(c16) --restore rc==0", _restore.returncode == 0,
      f"rc={_restore.returncode} stderr={_restore.stderr.strip()[-300:]}")
check("(c17) restore 至少恢复 1 个文件", int(_pe.get("count") or 0) >= 1,
      repr(_pe.get("count")))
check("(c18) 被恢复文件回到原位", _old[0].is_file() and _result4.is_file(),
      f"{_old[0].exists()} {_result4.exists()}")

# --all-reports：默认保留最新一组，显式全清
_issue5 = Path(tempfile.mkdtemp(prefix="slcn_b121_all_")) / "2026年第5期(90)"
_issue5.mkdir()
for _p in (
    [_issue5 / f"信息检查2026_01_01_00_00_00.{e}" for e in ("txt", "json")]
    + [_issue5 / "信息检查2026_01_01_00_00_00_摘要.md",
       _issue5 / "信息检查2026_11_04_22_10_40.txt",
       _issue5 / "信息检查2026_11_04_22_10_40.json",
       _issue5 / "信息检查2026_11_04_22_10_40_摘要.md"]
):
    _p.write_text("R", encoding="utf-8")
_pd2 = json_tail(run([str(ENGINE / "cleantemp.py"), "--folder", str(_issue5),
                      "--mode", "tocwork", "--dry-run", "--json"]))
check("(c19) 默认保留最新一组 ⇒ 3 条旧报告", _pd2.get("count") == 3, repr(_pd2.get("count")))
_pd3 = json_tail(run([str(ENGINE / "cleantemp.py"), "--folder", str(_issue5),
                      "--mode", "tocwork", "--dry-run", "--all-reports", "--json"]))
check("(c20) --all-reports 连最新一组全清 ⇒ 6 条",
      _pd3.get("count") == 6, repr(_pd3.get("count")))
_aux = run([str(ENGINE / "cleantemp.py"), "--folder", str(_issue5),
            "--mode", "aux", "--all-reports", "--json"])
check("(c21) --all-reports 只属于 tocwork（aux 用 → rc2 报错）",
      _aux.returncode == 2 and "tocwork" in (_aux.stderr or ""),
      f"rc={_aux.returncode} stderr={_aux.stderr.strip()[-200:]}")

# ── (d) B1 依赖：tocdelivery 先找 .toolbox-tmp，再回退期目录根 ─────────────
print("\n── (d) B1 依赖：newest_timestamped_tex / place_tex 兼容两种落点 ──")
from slcn import tocdelivery as TD  # noqa: E402
from slcn import toc as _T  # noqa: E402

check("(d1) 隐藏工作目录常量双源一致",
      TD._TOC_WORK_DIR == _T.TOC_WORK_DIR == ".toolbox-tmp",
      f"{TD._TOC_WORK_DIR!r} vs {_T.TOC_WORK_DIR!r}")


def _write_at(path: Path, text: str = "X") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# new layout：只有隐藏目录里有
_issue6 = Path(tempfile.mkdtemp(prefix="slcn_b121_td_new_")) / "2026年第5期(90)"
_issue6.mkdir()
_new_work = _write_at(_issue6 / ".toolbox-tmp" / "Chinese Content_2026-11-04_23-30-00.tex")
_toc_dir6 = _issue6 / "000 Table of Contents"
_toc_dir6.mkdir()
check("(d2) new layout：取 .toolbox-tmp 下最新",
      TD.newest_timestamped_tex(str(_issue6), "Chinese Content") == str(_new_work),
      TD.newest_timestamped_tex(str(_issue6), "Chinese Content"))
_placed6 = TD.place_tex(str(_issue6), str(_toc_dir6), stems=(("zh", "Chinese Content"),))
check("(d3) new layout：place_tex 可落位固定名",
      Path(_placed6["zh"]) == _toc_dir6 / "Chinese Content.tex"
      and (_toc_dir6 / "Chinese Content.tex").is_file(), repr(_placed6))

# legacy layout：只有期目录根旧散落文件（兼容历史）
_issue7 = Path(tempfile.mkdtemp(prefix="slcn_b121_td_old_")) / "2026年第5期(90)"
_issue7.mkdir()
_old_tex = _write_at(_issue7 / "Chinese Content_2026-10-01_10-00-00.tex")
check("(d4) legacy layout：回退期目录根",
      TD.newest_timestamped_tex(str(_issue7), "Chinese Content") == str(_old_tex),
      TD.newest_timestamped_tex(str(_issue7), "Chinese Content"))

# 两者并存：优先隐藏工作目录（旧根文件即使 mtime 更新也不抢）
import os as _os  # noqa: E402
_issue8 = Path(tempfile.mkdtemp(prefix="slcn_b121_td_both_")) / "2026年第5期(90)"
_issue8.mkdir()
_work8 = _write_at(_issue8 / ".toolbox-tmp" / "Chinese Content_2026-11-04_23-30-00.tex")
_root8 = _write_at(_issue8 / "Chinese Content_2026-11-05_00-00-00.tex")
_old7 = _issue8 / "Chinese Content_2026-01-01_00-00-00.tex"
_old7.write_text("OLD", encoding="utf-8")
_os.utime(_old7, (2_000_000_000, 2_000_000_000))  # 让旧根文件 mtime 更新
check("(d5) 两者并存：优先 .toolbox-tmp（即便根文件 mtime 更新）",
      TD.newest_timestamped_tex(str(_issue8), "Chinese Content") == str(_work8),
      TD.newest_timestamped_tex(str(_issue8), "Chinese Content"))

# 两处都没有：place_tex 报「请先生成目录」
_issue9 = Path(tempfile.mkdtemp(prefix="slcn_b121_td_none_")) / "2026年第5期(90)"
_issue9.mkdir()
try:
    TD.place_tex(str(_issue9), str(_issue9 / "000 Table of Contents"), stems=(("zh", "Chinese Content"),))
    _err = ""
except TD.DeliveryError as exc:
    _err = str(exc)
check("(d6) 两处都没有：报错提示先生成目录",
      "请先生成目录" in _err and ".toolbox-tmp" in _err, repr(_err))


print(f"\n结果：{_passed} passed / {len(_fails)} failed")
if _fails:
    for name in _fails:
        print("  FAILED:", name)
    sys.exit(1)
