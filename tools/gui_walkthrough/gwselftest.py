"""gwselftest.py —— `gui_walkthrough.py --self-test` 的 hermetic 用例（≥12 例）。

hermetic 的机器证明：跑用例前把 `subprocess.run` 换成「一调用就抛 _Sentinel」的哨兵，
任何漏网的真实子进程（= 起 App / 读真实库 / 联网 / 写真实 defaults 的必经之路）都会让自测红。
用例只写 tempfile；helper 编译用注入的假 runner（不调 swiftc）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from gwdriver import _clip, _text_candidates  # noqa: E402
from gwcore import (
    ARCHIVE_PREFIX, AX_MAX_DEPTH, EXIT_ASSERT, EXIT_ENV, EXIT_NO_PERM, EXIT_OK, EXIT_USAGE,
    HELPER_SOURCE, SLUG_MAX, STATE_NAME, TOOL_VERSION, VERDICT_JSON, VERDICT_TXT,
    AssertError, EnvProblem, Evidence, PermissionProblem, UsageError,
    build_helper, choose_alert, choose_button, evidence_name, fixture_plan, guard_fixture_root,
    locator_argv, match_text, permission_hint, run_plan, screencapture_argv, sha256_file,
    slugify, snapshot_equal, status_digest, teardown_plan, validate_alert_args,
    validate_locator, window_rect, frontmost_script, keystroke_script, pbcopy_argv,
    parse_helper_output, render_dump, ax_tree_degenerate, validate_optional_locator, shot_tmp_name,
    filter_deepest, button_path_sane, pick_pid, validate_evidence_dir,
    DEGENERATE_AX_HINT,
)
class _Sentinel(Exception):
    pass


def _fake_run_factory(plan_records: list[tuple], results: dict | None = None):
    """构造假 runner（self-test 用）：记录调用、可选预置返回码。"""
    from types import SimpleNamespace

    def _run(argv, capture_output=False, text=True, timeout=None, input=None, **kw):
        plan_records.append(tuple(argv) if isinstance(argv, (list, tuple)) else (argv,))
        spec = (results or {}).get(tuple(argv), {})
        rc = spec.get("rc", 0)
        out_path = None
        if "-o" in argv:
            out_path = argv[argv.index("-o") + 1]
        if out_path and rc == 0 and spec.get("write_target", True):
            Path(out_path).write_text("fake-binary", encoding="utf-8")
        return SimpleNamespace(returncode=rc, stdout=spec.get("out", ""), stderr=spec.get("err", ""))

    return _run


def self_test() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, fn):
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001 —— 自测要把失败原因说清楚
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, msg))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}：{msg}")

    print(f"[gui_walkthrough self-test v{TOOL_VERSION}] hermetic（subprocess.run 已被哨兵替换）")

    # ---- 哨兵：任何真实子进程（进而任何网络/App 调用）都会红 ----
    real_run = subprocess.run

    def _forbidden(*a, **kw):  # pragma: no cover - 只为证明「没调用」
        raise _Sentinel(f"self-test 期间不允许真实子进程：{a[:1]}")

    subprocess.run = _forbidden
    try:
        # 1) evidence 命名 = 固定格式
        def t_naming():
            n1 = evidence_name(3, "select", "dai-chuli", "log")
            n2 = evidence_name(100, "shot", "窗口-1", "png")
            ok = (n1 == "03-select-dai-chuli.log" and n2.startswith("100-shot-")
                  and VERDICT_TXT == "VERDICT-SUMMARY.txt" and VERDICT_JSON.endswith(".json"))
            return ok, f"{n1} / {n2} / {VERDICT_TXT}"

        # 2) slug 化（CJK 保留、路径分隔符消掉、长度上限）
        def t_slug():
            a = slugify("待处理 / 字段归位 1 条")
            b = slugify("")
            c = slugify("a" * 80)
            ok = (a == "待处理-字段归位-1-条" and b == "step" and len(c) <= SLUG_MAX
                  and "/" not in a)
            return ok, f"{a!r} / {b!r} / len={len(c)}"

        # 3) helper 输出解析（容忍噪声行 / 坏 JSON）
        def t_parse():
            d1 = parse_helper_output('warning: x\n{"ok": true, "count": 2}\n', "", 0)
            d2 = parse_helper_output("not json", "boom", 3)
            d3 = parse_helper_output("", "no stdout", 4)
            ok = (d1["ok"] and d1["count"] == 2 and d2["error"] == "bad_json"
                  and d2["_rc"] == 3 and d3["error"] == "no_output")
            return ok, f"ok={d1['ok']} / {d2['error']} / {d3['error']}"

        # 4) 定位 argv 构造（名称定位 vs 路径定位）
        def t_locator_argv():
            a = locator_argv({"role": "AXButton", "name": "推送…", "nth": 2, "exact": True,
                              "max_depth": 12})
            b = locator_argv({"path": "0.6.3", "role": "AXButton"})
            ok = (a == ["--role", "AXButton", "--name", "推送…", "--nth", "2", "--exact",
                        "--max-depth", "12"] and b == ["--path", "0.6.3"])
            return ok, f"{a} / {b}"

        # 5) 定位参数护栏（path 与名称互斥、空定位、坏路径）
        def t_locator_guard():
            cases = [{"path": "0.1", "role": "AXButton"}, {}, {"path": "0,1"}]
            codes = []
            for c in cases:
                try:
                    validate_locator(c)
                    codes.append("ok")
                except UsageError:
                    codes.append("usage")
            ok = codes == ["usage", "usage", "usage"]
            return ok, f"{codes}"

        # 6) alert 解析：B-190 真实形态（sheet desc=alert + 文本 + 按钮 desc）
        def t_alert_b190():
            alerts = [{
                "path": "0.6", "role": "AXSheet", "desc": "alert",
                "texts": ["推送到远端？", "把当前分支未推送的提交推到远端。"],
                "buttons": [{"path": "0.6.2", "name": "取消", "desc": "取消"},
                            {"path": "0.6.3", "name": "推送", "desc": "推送"}],
            }]
            alert, err = choose_alert(alerts, "推送到远端？")
            btn, berr = choose_button(alert, "推送") if alert else (None, err)
            ok = (alert is not None and alert.get("path") == "0.6"
                  and btn is not None and btn.get("path") == "0.6.3" and not berr)
            return ok, f"alert={alert and alert.get('path')} button={btn and btn.get('path')}"

        # 7) alert 按钮多命中 → 拒绝（不按索引猜）
        def t_alert_ambiguous():
            alert = {"path": "0.6", "texts": ["确认？"],
                     "buttons": [{"path": "0.6.2", "name": "取消"},
                                 {"path": "0.6.3", "name": "取消所有"}]}
            btn, err = choose_button(alert, "取消", exact=False)
            ok = btn is None and "命中 2" in err
            return ok, f"{err[:60]}"

        # 8) alert 标题不匹配 → 明确失败（列出当前 alert 文本）
        def t_alert_title_miss():
            alerts = [{"path": "0.6", "texts": ["手动提交库文件？"],
                       "buttons": [{"path": "0.6.2", "name": "取消"}]}]
            alert, err = choose_alert(alerts, "回退到上一个提交？")
            ok = alert is None and "手动提交库文件？" in err
            return ok, f"{err[:70]}"

        # 9) 禁索引点击（B-190 教训写进参数校验）
        def t_alert_no_index():
            ns = argparse.Namespace(index=3, title="x", click=True, button=None)
            try:
                validate_alert_args(ns)
                return False, "没有拒绝 --index"
            except UsageError as exc:
                msg = str(exc)
                return ("B-190" in msg and "索引" in msg), msg[:70]

        # 10) 文本断言语义（contains / equals / ignore_case）
        def t_text_match():
            hay = "库 sha 364946b9…｜待处理 18"
            ok = (match_text("库 sha", hay) and not match_text("库 sha", hay, exact=True)
                  and match_text(hay, hay, exact=True) and match_text("库 SHA", hay, ignore_case=True)
                  and not match_text("库 SHA", hay))
            return ok, "contains/equals/ignore_case 四个方向都对"

        # 11) 文本候选抽取 + within 前缀过滤
        def t_text_candidates():
            entries = [
                {"path": "0.0", "role": "AXStaticText", "fields": {"value": "分支 main"}},
                {"path": "0.1", "role": "AXStaticText", "fields": {"value": "未推送提交：0 条"}},
            ]
            allc = _text_candidates(entries)
            sub = _text_candidates(entries, within="0.1")
            ok = (len(allc) == 2 and len(sub) == 1 and sub[0]["text"].startswith("未推送"))
            return ok, f"all={len(allc)} within={len(sub)}"

        # 12) VERDICT 汇总（PASS/FAIL/SKIP 语义 + 证据路径）
        def t_verdict():
            with tempfile.TemporaryDirectory(prefix="gw-ev-") as td:
                ev = Evidence(Path(td))
                ev.record("select", "a", "PASS", ms=10, note="ok", text_log="x\n")
                ev.record("shot", "b", "PASS", ms=20, text_log="y\n")
                ev.record("fixture", "c", "SKIP", ms=1, note="dry-run", text_log="z\n")
                text, obj = ev.write_summary()
                ok1 = (obj["verdict"] == "PASS" and obj["counts"]["PASS"] == 2
                       and obj["counts"]["SKIP"] == 1 and "VERDICT: PASS" in text
                       and "01-select-a.log" in text and (Path(td) / VERDICT_JSON).is_file())
                ev.record("assert-text", "d", "FAIL", ms=5, note="missing", text_log="w\n")
                text2, obj2 = ev.write_summary()
                ok2 = obj2["verdict"] == "FAIL" and "VERDICT: FAIL" in text2
                return (ok1 and ok2), f"pass→{obj['verdict']} fail→{obj2['verdict']}"

        # 13) evidence 续编号 + --reset 归档
        def t_evidence_seq():
            with tempfile.TemporaryDirectory(prefix="gw-ev2-") as td:
                d = Path(td)
                ev = Evidence(d)
                e1 = ev.record("select", "a", "PASS", ms=1, text_log="1\n")
                e2 = ev.record("click", "b", "PASS", ms=1, text_log="2\n")
                ev2 = Evidence(d)
                e3 = ev2.record("dump", "c", "PASS", ms=1, text_log="3\n")
                ev3 = Evidence(d, reset=True)
                e4 = ev3.record("select", "z", "PASS", ms=1, text_log="4\n")
                archive = [p for p in d.iterdir() if p.is_dir() and p.name.startswith(ARCHIVE_PREFIX)]
                archived = list(archive[0].iterdir()) if archive else []
                ok = (e1["seq"] == 1 and e2["seq"] == 2 and e3["seq"] == 3 and e4["seq"] == 1
                      and len(archived) >= 5)
                return ok, f"seq={e1['seq']},{e2['seq']},{e3['seq']} reset→{e4['seq']} archived={len(archived)}"

        # 14) fixture 计划（顺序 + argv；只构造不执行）
        def t_fixture_plan():
            calls = []
            plan = fixture_plan(Path(tempfile.gettempdir()) / "gw-fx-plan", Path("/x/notes"),
                                "cn.edu.test.domain", "notesRepoPath")
            sub = [s["argv"][:3] for s in plan]
            ok = ([c[0] for c in sub] == ["git", "git", "defaults", "defaults"]
                  and plan[0]["argv"][1:4] == ["clone", "--bare", "/x/notes"]
                  and plan[2]["argv"][:4] == ["defaults", "write", "cn.edu.test.domain", "notesRepoPath"]
                  and plan[3]["argv"][:2] == ["defaults", "read"] and calls == [])
            return ok, f"{[s['argv'][:4] for s in plan]}"

        # 15) fixture 根护栏（只准 /tmp；不许嵌套真实 notes）
        def t_fixture_guard():
            notes = Path("/Users/x/EditToolBox-notes")
            bad = [Path("/Users/x/gw"), Path("/Users/x/EditToolBox-notes/gw")]
            good = Path(tempfile.gettempdir()) / "gw-ok"
            codes = []
            for root in bad:
                try:
                    guard_fixture_root(root, notes)
                    codes.append("ok")
                except UsageError:
                    codes.append("usage")
            allowed = guard_fixture_root(good, notes)
            ok = codes == ["usage", "usage"] and str(allowed).startswith(
                os.path.realpath(tempfile.gettempdir()))
            return ok, f"{codes} allowed={allowed}"

        # 16) teardown 计划（有原值还原 / 无原值 delete；最后清理）
        def t_teardown_plan():
            root = Path(tempfile.gettempdir()) / "gw-td"
            del_plan = teardown_plan(root, "dom", "key", None)
            keep_plan = teardown_plan(root, "dom", "key", "/old/notes")
            ok = (del_plan[0]["argv"] == ["defaults", "delete", "dom", "key"]
                  and keep_plan[0]["argv"] == ["defaults", "write", "dom", "key", "/old/notes"]
                  and del_plan[-1]["argv"][:3] == ["rm", "-rf", str(Path(os.path.realpath(root)))]
                  and del_plan[1]["allow_rc"] == (0, 1))
            return ok, f"{del_plan[0]['argv']} / {keep_plan[0]['argv']}"

        # 17) 计划执行器：失败即停（后续步骤不跑）
        def t_run_plan_abort():
            calls = []
            plan = [{"argv": ["echo", "1"], "note": "a"},
                    {"argv": ["false"], "note": "b"},
                    {"argv": ["echo", "3"], "note": "c"}]
            fake = _fake_run_factory(calls, {("false",): {"rc": 1, "err": "nope"}})
            try:
                run_plan(plan, runner=fake)
                return False, "没有抛 EnvProblem"
            except EnvProblem as exc:
                return (len(calls) == 2 and "b 失败" in str(exc)), f"calls={len(calls)} msg={str(exc)[:50]}"

        # 18) 权限提示 + 退出码语义（缺权不静默 PASS）
        def t_permission():
            hint = permission_hint("dsh")
            codes = {EXIT_OK, EXIT_ASSERT, EXIT_USAGE, EXIT_NO_PERM, EXIT_ENV}
            ok = ("系统设置" in hint and "隐私与安全性" in hint and "辅助功能" in hint
                  and "dsh" in hint and "PASS" in hint and len(codes) == 5
                  and AssertError("x").code == EXIT_ASSERT and EnvProblem("x").code == EXIT_ENV
                  and UsageError("x").code == EXIT_USAGE
                  and PermissionProblem("x").code == EXIT_NO_PERM)
            return ok, f"hint {len(hint)} 字 / codes={sorted(codes)}"

        # 19) 截图 argv（窗口 rect / 整屏 / 坏 rect）
        def t_shot_argv():
            out = Path("/tmp/x.png")
            a = screencapture_argv((603, 318, 920, 464), out)
            b = screencapture_argv(None, out)
            c = window_rect({"x": 603.4, "y": 317.6, "w": 920.2, "h": 464.4})
            try:
                screencapture_argv((0, 0, 0, 10), out)
                bad = "no-error"
            except EnvProblem:
                bad = "env"
            ok = (a == ["screencapture", "-x", "-o", "-R", "603,318,920,464", str(out)]
                  and b == ["screencapture", "-x", "-o", str(out)] and c == (603, 318, 920, 464)
                  and bad == "env")
            return ok, f"{a[3:5]} / full={b[3]} / {c} / {bad}"

        # 20) AppleScript / pbcopy 命令构造
        def t_osa_scripts():
            s1 = frontmost_script("Toolbox")
            s2 = keystroke_script("a")
            s3 = keystroke_script('v"x')
            ok = ('process "Toolbox"' in s1 and 'frontmost to true' in s1
                  and s2 == 'tell application "System Events" to keystroke "a" using command down'
                  and '\\"' in s3 and pbcopy_argv() == ["pbcopy"])
            return ok, f"{s1[:40]}… / {s2[-30:]}"

        # 21) helper 编译缓存（假 runner 造二进制；第二次复用不重编）
        def t_helper_build():
            calls = []
            with tempfile.TemporaryDirectory(prefix="gw-helper-") as td:
                src = Path(td) / "h.swift"
                src.write_text("// fake swift source\n", encoding="utf-8")
                cache = Path(td) / "cache"
                fake = _fake_run_factory(calls)
                p1 = build_helper(src, cache, runner=fake)
                p2 = build_helper(src, cache, runner=fake)
                ok = (p1 == p2 and p1.is_file() and len(calls) == 1
                      and os.access(p1, os.X_OK))
                return ok, f"cache={p1.name} compiles={len(calls)}"

        # 22) helper 源码契约（自查：系统框架 + 无索引点击 + 名字复核）
        def t_helper_source():
            src = HELPER_SOURCE.read_text(encoding="utf-8")
            check_bits = ["AXIsProcessTrusted", "AXUIElementPerformAction", "AXValue",
                          "kAXPressAction", "--expect-name", "alerts", "AXSheet",
                          "apply_rule", "--index"]
            ok = (all(b in src for b in check_bits[:7]) and "prefix: path" in src
                  and "--index" not in src
                  and "cocoa" not in src.lower() and "import AppKit" not in src)
            return ok, f"{len(src.splitlines())} 行 / 系统框架 only"

        # 23) 树 dump 渲染（缩进 + 截断标记；纯函数）
        def t_dump_render():
            text = render_dump([
                {"path": "", "depth": 0, "role": "AXApplication", "title": "工具箱"},
                {"path": "0", "depth": 1, "role": "AXWindow", "title": "编辑部工具箱"},
                {"path": "0.0", "depth": 2, "role": "AXStaticText", "value": "x" * 300},
                {"path": "0.1", "depth": 1, "role": "AXButton", "selected": True, "desc": "推送…"},
            ], max_depth=3)
            lines = text.splitlines()
            ok = (lines[0].endswith("（count=4 max_depth=3）") and lines[2].startswith("  [0] | AXWindow")
                  and lines[1].startswith("[] | AXApplication")
                  and lines[3].startswith("    [0.0] | AXStaticText") and "…[+" in lines[3]
                  and lines[4].endswith("SELECTED") and "desc=推送…" in lines[4])
            return ok, f"{len(lines)} 行；缩进/截断/SELECTED 均对"

        # 24) 快照比对（sha / 状态摘要）
        def t_snapshot():
            with tempfile.TemporaryDirectory(prefix="gw-snap-") as td:
                f = Path(td) / "db.jsonl"
                f.write_text("a\n", encoding="utf-8")
                s1 = sha256_file(f)
                f.write_text("b\n", encoding="utf-8")
                s2 = sha256_file(f)
                a = {"head": "h1", "status_digest": status_digest(""), "db_sha256": s1}
                b = dict(a)
                same, diffs = snapshot_equal(a, b)
                c = dict(a, db_sha256=s2)
                diff2, fields = snapshot_equal(a, c)
                ok = (s1 != s2 and same and not diffs and not diff2 and fields == ["db_sha256"])
                return ok, f"sha 变={s1 != s2} 同={same} 差异={fields}"

        # 25) 退化 AX 树识别（屏幕休眠形态）+ 可选定位校验
        def t_degenerate():
            degen = ["AXApplication"] * 6
            normal = ["AXApplication", "AXWindow", "AXSplitGroup", "AXButton"]
            small = ["AXApplication"] * 3
            validate_optional_locator({"role": "", "name": ""})  # 不抛
            try:
                validate_optional_locator({"role": "AXButton", "path": "0.1"})
                guard = "no-error"
            except UsageError:
                guard = "usage"
            ok = (ax_tree_degenerate(degen) and not ax_tree_degenerate(normal)
                  and not ax_tree_degenerate(small) and guard == "usage"
                  and "caffeinate" in DEGENERATE_AX_HINT
                  and button_path_sane("0.6", "0.6.3") and not button_path_sane("0.6", "3"))
            return ok, f"degen={ax_tree_degenerate(degen)} normal={ax_tree_degenerate(normal)} guard={guard}"

        # 26) 截图临时文件名（screencapture 拒绝 '.' 开头 → B-191 实测踩坑）
        def t_shot_tmp_name():
            name = shot_tmp_name(4242)
            ok = (name == "tmp-shot-4242.png" and not name.startswith(".") and name.endswith(".png"))
            return ok, name

        # 27) --subtree 最深命中过滤（祖先/行/单元格都会命中时的取舍）
        def t_filter_deepest():
            ms = [{"path": "0"}, {"path": "0.0"}, {"path": "0.0.0.5"},
                  {"path": "0.2"}, {"path": "0.2.1"}]
            got = [m["path"] for m in filter_deepest(ms)]
            ok = got == ["0.0.0.5", "0.2.1"]
            return ok, f"{got}"

        # 28) pid 精确匹配（不能把「命令行含 bundle 路径」的 bash 当 App）
        def t_pick_pid():
            exe = "/A/编辑部工具箱.app/Contents/MacOS/Toolbox"
            rows = [(111, "bash -c cd /A && open -n 编辑部工具箱.app"), (222, exe), (333, exe + " --arg")]
            ok = (pick_pid(rows, exe) == 222 and pick_pid(rows[:1], exe) is None
                  and pick_pid([], exe) is None)
            return ok, f"picked={pick_pid(rows, exe)}"

        # 29) 空 --evidence-dir 拒绝（Path("") = cwd，会把证据写进仓库根——B-191 实测踩坑）
        def t_empty_evidence_dir():
            try:
                Evidence("")
                return False, "没有拒绝空 evidence-dir"
            except UsageError as exc:
                return ("evidence-dir" in str(exc)), str(exc)[:50]

        # 30) evidence-dir 只收绝对路径（V1 F2：`.`/相对路径会写进 cwd、污染工作树）
        def t_evidence_dir_absolute_only():
            bad = [".", "./x", "a/b", "../tmp/x", "ev"]
            msgs = []
            for value in bad:
                try:
                    validate_evidence_dir(value)
                    msgs.append("")
                except UsageError as exc:
                    msgs.append(str(exc))
            good = validate_evidence_dir("/tmp/gw-evd")
            tilde = validate_evidence_dir("~/gw-evd")
            try:
                validate_evidence_dir("")
                empty = "ok"
            except UsageError:
                empty = "usage"
            ok = (all("绝对路径" in m for m in msgs) and good == Path("/tmp/gw-evd")
                  and tilde.is_absolute() and empty == "usage")
            return ok, f"bad={len(bad)} 全拒 / good={good} / empty={empty}"

        for name, fn in [
            ("evidence_naming", t_naming), ("slug", t_slug), ("helper_output_parse", t_parse),
            ("locator_argv", t_locator_argv), ("locator_guard", t_locator_guard),
            ("alert_b190_shape", t_alert_b190), ("alert_button_ambiguous", t_alert_ambiguous),
            ("alert_title_miss", t_alert_title_miss), ("alert_no_index", t_alert_no_index),
            ("text_match", t_text_match), ("text_candidates", t_text_candidates),
            ("verdict_summary", t_verdict), ("evidence_seq_reset", t_evidence_seq),
            ("fixture_plan", t_fixture_plan), ("fixture_root_guard", t_fixture_guard),
            ("teardown_plan", t_teardown_plan), ("run_plan_abort", t_run_plan_abort),
            ("permission_and_exit_codes", t_permission), ("shot_argv", t_shot_argv),
            ("osa_scripts", t_osa_scripts), ("helper_build_cache", t_helper_build),
            ("helper_source_contract", t_helper_source), ("dump_render", t_dump_render),
            ("snapshot_equal", t_snapshot), ("degenerate_ax_tree", t_degenerate), ("shot_tmp_name", t_shot_tmp_name), ("filter_deepest", t_filter_deepest), ("pick_pid", t_pick_pid), ("empty_evidence_dir", t_empty_evidence_dir), ("evidence_dir_absolute_only", t_evidence_dir_absolute_only),
        ]:
            check(name, fn)
    finally:
        subprocess.run = real_run

    ok = sum(1 for _, passed, _ in results if passed)
    print(f"[gui_walkthrough self-test] {ok}/{len(results)} PASS"
          + ("" if ok == len(results) else " ← 有失败"))
    print("[gui_walkthrough self-test] hermetic：全程 0 个真实子进程（哨兵未触发）、"
          "未起 App、未读真实库、未联网、未写真实 App defaults")
    return 0 if ok == len(results) else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

