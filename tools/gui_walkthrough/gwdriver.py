"""gwdriver.py —— gui_walkthrough 的动作层：Ctx（App/pid/AX/AppleScript）与 9 个子命令实现。

只依赖 gwcore 的纯函数与常量；不解析命令行（入口在 `gui_walkthrough.py`）。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from gwcore import (  # noqa: F401 —— 常量/纯函数全部来自 gwcore，驱动层只做动作
    AX_MAX_DEPTH, DEFAULT_APP, DEFAULT_DOMAIN, DEFAULT_KEY, DEFAULT_NOTES_REPO,
    EXIT_ENV, EXIT_OK, EXIT_USAGE, AssertError, EnvProblem, Evidence, PermissionProblem,
    STATE_NAME, ToolError, UsageError, _clip, _now, alert_titles, build_helper, choose_alert,
    choose_button, collapse_nested_alerts, element_name, fixture_plan, guard_fixture_root,
    alert_press_verdict,
    helper_call,
    frontmost_script, keystroke_script, locator_argv, match_text, pbcopy_argv, permission_hint, run_cmd,
    ax_tree_degenerate, button_path_sane, filter_deepest, pick_pid, render_dump, run_plan, screencapture_argv, sha256_file, shot_tmp_name,
    snapshot_equal, snapshot_repo, validate_optional_locator, DEGENERATE_AX_HINT,
    status_digest, teardown_plan, validate_alert_args, validate_locator, window_rect,
)
class Ctx:
    def __init__(self, ns):
        self.ns = ns
        self.evidence = Evidence(
            Path(ns.evidence_dir), app=ns.app or "", pid=ns.pid, process=ns.process_name or "",
            reset=ns.reset_evidence)
        self._helper: Path | None = Path(ns.helper) if getattr(ns, "helper", None) else None
        self._pid: int | None = ns.pid
        self._process: str = ns.process_name or ""
        self._trusted: bool | None = None

    # -- 环境 ---------------------------------------------------------------
    def app_path(self) -> Path:
        return Path(self.ns.app).expanduser().resolve() if self.ns.app else DEFAULT_APP

    def process_name(self) -> str:
        if self._process:
            return self._process
        exe = self.app_path() / "Contents" / "MacOS"
        try:
            names = [p.name for p in exe.iterdir() if p.is_file()]
        except OSError:
            names = []
        self._process = names[0] if names else "Toolbox"
        return self._process

    def pid(self) -> int:
        if self._pid:
            return int(self._pid)
        app = self.app_path()
        exe = app / "Contents" / "MacOS" / self.process_name()
        proc = run_cmd(["pgrep", "-x", exe.name])
        pids = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip().isdigit()]
        rows: list[tuple[int, str]] = []
        for pid in pids:
            ps = run_cmd(["ps", "-o", "command=", "-p", pid])
            rows.append((int(pid), ps.stdout or ""))
        chosen = pick_pid(rows, str(exe))
        if chosen is None:
            raise EnvProblem(f"App 未启动：{app}\n  → 先 `open \"{app}\"`，或用 --pid 指定进程")
        self._pid = chosen
        self._pid = int(pids[0])
        self.evidence.state["pid"] = self._pid
        self.evidence.state["app"] = str(app)
        self.evidence.state["process"] = self.process_name()
        self.evidence._save()
        return self._pid

    def helper_path(self) -> Path:
        if self._helper is None:
            self._helper = build_helper()
        return self._helper

    def helper(self, args: list[str], *, timeout: int = 60) -> dict:
        data = helper_call(args, helper=self.helper_path(), timeout=timeout)
        if data.get("error") == "not_trusted":
            raise PermissionProblem(permission_hint(self.process_name()), data)
        return data

    def maybe_frontmost(self) -> None:
        """按 --frontmost 把 App 切前台（best-effort：失败不阻断，只记证据）。"""
        if not getattr(self.ns, "frontmost", False):
            return
        try:
            self.osa(frontmost_script(self.process_name()))
            self.evidence.note("frontmost: on（AppleScript 已切前台）")
        except ToolError as exc:
            self.evidence.note(f"frontmost 失败（忽略）：{exc}")

    def require_ax(self) -> None:
        if self._trusted is None:
            data = self.helper(["trusted"])
            self._trusted = bool(data.get("trusted"))
            if not any(n.startswith("AX 权限") for n in self.evidence.state.get("notes", [])):
                self.evidence.note(f"AX 权限: {'trusted' if self._trusted else 'NOT trusted'}")
            self.evidence.state.setdefault("trusted", self._trusted)
        if not self._trusted:
            raise PermissionProblem(permission_hint(self.process_name()))

    # -- 动作 ---------------------------------------------------------------
    def ax(self, cmd: str, loc: dict, extra: list[str] | None = None) -> dict:
        self.require_ax()
        argv = [cmd, "--pid", str(self.pid())] + locator_argv(loc) + (extra or [])
        return self.helper(argv)

    def osa(self, script: str) -> str:
        proc = run_cmd(["osascript", "-e", script])
        if proc.returncode != 0:
            raise EnvProblem(f"osascript 失败（rc={proc.returncode}）：{(proc.stderr or '').strip()[:300]}")
        return (proc.stdout or "").strip()

    def pbcopy(self, text: str) -> None:
        proc = subprocess.run(pbcopy_argv(), input=text.encode("utf-8"))
        if proc.returncode != 0:
            raise EnvProblem(f"pbcopy 失败（rc={proc.returncode}）")

    def read_value(self, path: str) -> str:
        data = self.helper(["resolve", "--pid", str(self.pid()), "--path", path])
        matches = data.get("matches") or []
        return (matches[0].get("value", "") if matches else "") or ""

    def poll(self, fn, timeout: float, interval: float = 0.2):
        deadline = time.monotonic() + max(0.0, timeout)
        last = None
        while True:
            last = fn()
            if last[0]:
                return last
            if time.monotonic() >= deadline:
                return last
            time.sleep(interval)


def _has_non_ascii(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text or "")


def _resolve_one(ctx: Ctx, loc: dict) -> dict:
    data = ctx.ax("resolve", loc)
    if not data.get("ok"):
        raise AssertError(f"定位失败：{data.get('error')} {data.get('message', '')}", data)
    matches = data.get("matches") or []
    if loc.get("subtree"):
        matches = filter_deepest(matches)
    if not matches:
        raise AssertError("定位失败：0 命中", data)
    if len(matches) > 1 and loc.get("nth") is None:
        names = [f"{m.get('role')}/{(element_name(m) or '')[:24]}@{m.get('path')}" for m in matches[:6]]
        raise AssertError(f"命中 {len(matches)} 个元素，拒绝盲目操作：{names}（加 --nth 或更精确的 --name）", data)
    return matches[0]


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_select(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    ctx.maybe_frontmost()
    validate_locator(loc)
    el = _resolve_one(ctx, loc)
    extra = ["--fallback-press"] if ctx.ns.fallback_press else []
    data = ctx.ax("select", {"path": el["path"]}, extra)
    if not data.get("ok"):
        raise AssertError(f"select 失败：{data.get('error')} {data.get('message', '')}", data)
    verified = bool(data.get("verified"))
    note = f"{data.get('role')}「{_clip(data.get('name') or '', 60)}」path={data.get('path')}"
    log = (f"# select {_now()}\n目标: {note}\nverified={verified} "
           f"focused={data.get('focused')} selected={data.get('selected')}\n"
           f"fallback_press={data.get('fallback_press')}\n")
    if not verified and not ctx.ns.allow_unverified:
        raise AssertError(f"select 未生效（focused/selected 未置真）：{note}；"
                          f"可加 --fallback-press 或 --allow-unverified", {**data, "log": log})
    return "PASS", note + ("" if verified else "（未验证）"), {"select": data, "log": log}


def cmd_click(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    ctx.maybe_frontmost()
    validate_locator(loc)
    el = _resolve_one(ctx, loc)
    expect = element_name(el)
    extra = ["--expect-name", expect] if expect else []
    data = ctx.ax("press", {"path": el["path"]}, extra)
    log = (f"# click {_now()}\n点击: {data.get('role')}「{expect}」path={data.get('path')}\n"
           f"expect_name={expect!r}\n")
    if not data.get("ok"):
        raise AssertError(f"点击失败：{data.get('error')} {data.get('message', '')}", {**data, "log": log})
    return "PASS", f"点击 {data.get('role')}「{_clip(expect, 60)}」", {"click": data, "log": log}


def cmd_field(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    validate_locator(loc)
    ns = ctx.ns
    if ns.text_file:
        text = Path(ns.text_file).expanduser().read_text(encoding="utf-8")
        if text.endswith("\n"):
            text = text[:-1]
    else:
        text = ns.text if ns.text is not None else ""
    el = _resolve_one(ctx, loc)
    path = el["path"]
    method = ns.method
    if method == "auto":
        method = "paste" if _has_non_ascii(text) else "ax"
    used = method
    verified = False
    detail = ""
    if method == "ax":
        data = ctx.ax("setvalue", {"path": path}, ["--value", text])
        verified = bool(data.get("verified"))
        detail = f"ax setvalue verified={verified} readback={_clip(data.get('readback', ''), 120)!r}"
        if not verified and ns.method == "auto":
            method = "paste"
            used = "ax→paste"
    if method == "paste":
        ctx.require_ax()
        ctx.osa(frontmost_script(ctx.process_name()))
        ctx.ax("select", {"path": path}, ["--focus"])
        ctx.pbcopy(text)
        ctx.osa(keystroke_script("a"))
        ctx.osa(keystroke_script("v"))
        expected = _clip(text, 2000)

        def _check():
            got = _clip(ctx.read_value(path), 2000)
            return (got == expected, got)

        ok, got = ctx.poll(_check, ns.timeout)
        verified = ok
        detail += f" | paste verified={ok} readback={_clip(got, 120)!r}"
    log = (f"# field {_now()}\n目标: {el.get('role')} path={path}\nmethod={used}\n"
           f"text_bytes={len(text.encode('utf-8'))} non_ascii={_has_non_ascii(text)}\n{detail}\n")
    if not verified:
        raise AssertError(f"field 写入未验证通过（method={used}）：{detail}", {"field": {"path": path}, "log": log})
    return "PASS", f"写入 {len(text)} 字符（{used}）→ {path}", {"field": {"path": path, "method": used,
                                                                  "verified": verified}, "log": log}


def cmd_scroll(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    validate_locator(loc)
    if ctx.ns.value is None and ctx.ns.delta is None:
        raise UsageError("scroll 需要 --value 0..1 或 --delta（相对位移）")
    extra = (["--value", str(ctx.ns.value)] if ctx.ns.value is not None
             else ["--delta", str(ctx.ns.delta)])
    data = ctx.ax("scroll", loc, extra)
    log = (f"# scroll {_now()}\n区域: {data.get('area_path')} scrollbar={data.get('scrollbar_path')}\n"
           f"before={data.get('before')} requested={data.get('requested')} after={data.get('after')} "
           f"verified={data.get('verified')}\n")
    if not data.get("ok"):
        raise AssertError(f"scroll 失败：{data.get('error')} {data.get('message', '')}", {**data, "log": log})
    if not data.get("verified") and not ctx.ns.allow_unverified:
        raise AssertError(f"scroll 未生效：before={data.get('before')} after={data.get('after')}", {**data, "log": log})
    return "PASS", f"滚动到 {data.get('after')}（requested {data.get('requested')}）", {"scroll": data, "log": log}


def cmd_shot(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    ctx.maybe_frontmost()
    ns = ctx.ns
    out_png = Path(ctx.evidence.dir) / shot_tmp_name(os.getpid())
    rect = None
    window_info = {}
    if not ns.full:
        ctx.require_ax()
        loc2 = dict(loc, role=loc.get("role") or "AXWindow")
        validate_locator(loc2)
        data = ctx.ax("window", loc2)
        if not data.get("ok"):
            raise AssertError(f"取窗口失败：{data.get('error')} {data.get('message', '')}；{DEGENERATE_AX_HINT}", data)
        windows = data.get("windows") or []
        frame = (windows[0] or {}).get("frame") if windows else None
        if not frame:
            raise EnvProblem("AX frame 不可用（窗口 0 个或未暴露 position/size）；可退 --full 整屏")
        rect = window_rect(frame, pad=ns.pad)
        window_info = {"title": windows[0].get("title", ""), "frame": frame, "rect": list(rect)}
    proc = run_cmd(screencapture_argv(rect, out_png))
    log = (f"# shot {_now()}\nrect={rect} full={bool(ns.full)}\n"
           f"window={json.dumps(window_info, ensure_ascii=False)}\n"
           f"screencapture rc={proc.returncode} err={(proc.stderr or '').strip()[:300]}\n")
    if proc.returncode != 0 or not out_png.is_file() or out_png.stat().st_size == 0:
        out_png.unlink(missing_ok=True)
        hint = ("；" + DEGENERATE_AX_HINT) if "could not create image" in (proc.stderr or "") else ""
        raise AssertError(f"截图失败（rc={proc.returncode}）{hint}", {"shot": {"rect": rect}, "log": log})
    size = out_png.stat().st_size
    return "PASS", f"截图 {size // 1024} KB rect={rect}", {
        "shot": {"rect": rect, "bytes": size, "window": window_info}, "log": log,
        "_binary": ("png", out_png)}


def _text_candidates(entries: list[dict], within: str = "") -> list[dict]:
    out = []
    for e in entries:
        path = e.get("path", "")
        if within and not (path == within or path.startswith(within + ".")):
            continue
        for field, text in (e.get("fields") or {}).items():
            out.append({"path": path, "role": e.get("role", ""), "field": field, "text": text})
    return out


def cmd_assert_text(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    ctx.maybe_frontmost()
    ns = ctx.ns
    validate_optional_locator(loc)
    if ns.contains is None and ns.equals is None:
        raise UsageError("assert-text 需要 --contains 或 --equals")
    ctx.require_ax()

    def _probe():
        argv = ["texts", "--pid", str(ctx.pid()),
                "--max-depth", str(ns.max_depth or AX_MAX_DEPTH)]
        if loc.get("role"):
            argv += ["--role", str(loc["role"])]
        data = ctx.helper(argv)
        if not data.get("ok"):
            raise AssertError(f"取 AX 文本失败：{data.get('error')} {data.get('message', '')}", data)
        entries = data.get("entries") or []
        if ax_tree_degenerate([e.get("role", "") for e in entries]):
            raise EnvProblem(DEGENERATE_AX_HINT, {"assert_text": {"degenerate": True}})
        candidates = _text_candidates(entries, loc.get("within") or "")
        needle = ns.contains if ns.contains is not None else ns.equals
        hit = next((c for c in candidates
                    if match_text(needle, c["text"], exact=ns.equals is not None,
                                  ignore_case=ns.ignore_case)), None)
        return (hit is not None, {"hit": hit, "count": len(candidates), "probe": data.get("count", 0)})

    t0 = time.monotonic()
    ok, detail = ctx.poll(_probe, ns.timeout, interval=max(0.1, ns.timeout / 10 or 0.1))
    elapsed = time.monotonic() - t0
    needle = ns.contains if ns.contains is not None else ns.equals
    mode = "contains" if ns.contains is not None else "equals"
    log = (f"# assert-text {_now()}\nneedle({mode})={needle!r} ignore_case={bool(ns.ignore_case)}\n"
           f"within={loc.get('within') or '-'} timeout={ns.timeout}s elapsed={elapsed:.2f}s\n"
           f"candidates={detail.get('count')} elements={detail.get('probe')}\n"
           f"hit={json.dumps(detail.get('hit'), ensure_ascii=False)}\n")
    if not ok:
        raise AssertError(f"断言失败：没有文本 {mode} {needle!r}（{detail.get('count')} 条候选）",
                          {"assert_text": detail, "log": log})
    return "PASS", f"命中「{_clip(needle, 40)}」@ {detail['hit']['path']}", {
        "assert_text": detail, "log": log}


def cmd_assert_alert(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    ctx.maybe_frontmost()
    ns = ctx.ns
    validate_alert_args(ns)
    ctx.require_ax()

    def _probe():
        data = ctx.helper(["alerts", "--pid", str(ctx.pid()), "--max-depth", str(ns.max_depth or 8)])
        if not data.get("ok"):
            raise AssertError(f"取 alert 失败：{data.get('error')} {data.get('message', '')}", data)
        raw_alerts = data.get("alerts") or []
        # B-197（2026-10-23 真点：嵌套 sheet alert 误报多命中）：外层 editor sheet 的 texts
        # 递归含内嵌 alert 文本 → 先按 path 祖先-后代折叠，再按标题挑唯一；并列多命中仍拒绝。
        alerts = collapse_nested_alerts(raw_alerts)
        alert, err = choose_alert(alerts, ns.title, exact=ns.exact_title, body=ns.body or "",
                                  ignore_case=ns.ignore_case)
        return (alert is not None, {"alert": alert, "error": err, "seen": len(alerts),
                                    "seen_raw": len(raw_alerts),
                                    "titles": [t for a in alerts for t in alert_titles(a)[:2]]})

    t0 = time.monotonic()
    found, detail = ctx.poll(_probe, ns.timeout, interval=max(0.1, ns.timeout / 10 or 0.1))
    elapsed = time.monotonic() - t0
    log = (f"# assert-alert {_now()}\ntitle={ns.title!r} button={ns.button!r} click={bool(ns.click)}\n"
           f"timeout={ns.timeout}s elapsed={elapsed:.2f}s alerts_seen={detail.get('seen')} "
           f"(raw {detail.get('seen_raw')})\n")
    if not found:
        raise AssertError(f"alert 断言失败：{detail.get('error')}；若界面确有 alert：确认 App 在前台，"
                          f"屏幕休眠会让 AX 树退化（先 caffeinate -u -t 2）", {**detail, "log": log})
    alert = detail["alert"]
    log += f"alert_path={alert.get('path')} role={alert.get('role')}\n"
    log += "texts=" + json.dumps(alert.get("texts"), ensure_ascii=False) + "\n"
    log += "buttons=" + json.dumps([b.get("name") for b in alert.get("buttons", [])],
                                   ensure_ascii=False) + "\n"
    clicked = ""
    if ns.button:
        button, err = choose_button(alert, ns.button, exact=not ns.button_contains)
        if button is None:
            raise AssertError(f"alert 按钮断言失败：{err}", {**detail, "log": log + f"button_error={err}\n"})
        if not button_path_sane(alert.get("path", ""), button.get("path", "")):
            raise AssertError(f"按钮路径不是 alert 的后代绝对路径（alert={alert.get('path')} "
                              f"button={button.get('path')}）；拒绝点击", {**detail, "log": log})
        log += f"button_path={button.get('path')} button_name={button.get('name')!r}\n"
        clicked = button.get("name", "")
        if ns.click:
            press = ctx.helper(["press", "--pid", str(ctx.pid()), "--path", button["path"],
                                "--expect-name", button.get("name", "")])
            press_ok = bool(press.get("ok"))
            press_detail = press
            dismissed: bool | None = None
            if not ns.no_verify_dismiss:
                def _gone():
                    again = ctx.helper(["alerts", "--pid", str(ctx.pid()), "--max-depth", "8"])
                    still = [a for a in (again.get("alerts") or []) if a.get("path") == alert.get("path")]
                    return (not still, {"still": bool(still)})

                gone, gdetail = ctx.poll(_gone, ns.dismiss_timeout)
                dismissed = gone
                log += f"dismissed={gone} ({gdetail})\n"
            verdict, reason = alert_press_verdict(press_ok, dismissed,
                                                  not bool(ns.no_verify_dismiss))
            if not verdict:
                if reason == "press_error_unverified":
                    raise AssertError(
                        f"点 alert 按钮失败：{press.get('error')} {press.get('message', '')}",
                        {**detail, "press": press, "log": log})
                raise AssertError(
                    f"点 alert 按钮失败且 alert 仍在：{press.get('error')} "
                    f"{press.get('message', '')}" if not press_ok
                    else "点击后 alert 仍在（可能点空/嵌套 alert）",
                    {**detail, "press": press, "log": log})
            if reason == "tolerated":
                # B-197 附带：AXPress 报 -25205（SwiftUI .cancel 按钮怪癖）但 alert 已关
                log += (f"press_error_tolerated=True press={press.get('error')} "
                        f"{press.get('message', '')}\n")
    return "PASS", (f"alert「{_clip(ns.title, 30)}」" + (f" → 点「{clicked}」" if clicked else "")
                    + f"（{elapsed:.2f}s）"), {
        "assert_alert": detail, "alert_path": alert.get("path"), "clicked": clicked,
        "press": press_detail if ns.click and ns.button else None, "log": log}


def cmd_dump(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    ctx.maybe_frontmost()
    validate_optional_locator(loc)
    ctx.require_ax()
    data = ctx.ax("dump", loc if (loc.get("path") or loc.get("role") or loc.get("name")) else
                  {**loc, "max_depth": loc.get("max_depth") or 8})
    if not data.get("ok"):
        raise AssertError(f"dump 失败：{data.get('error')} {data.get('message', '')}", data)
    elements = data.get("elements") or []
    if ax_tree_degenerate([e.get("role", "") for e in elements]):
        raise EnvProblem(DEGENERATE_AX_HINT, {"dump": {"count": len(elements), "degenerate": True}})
    log = render_dump(elements, max_depth=loc.get("max_depth"),
                      header=f"# AX dump {_now()}")
    if getattr(ctx.ns, "print", False):
        print(log, end="")
    return "PASS", f"AX 树 {len(elements)} 个元素", {"dump": {"count": len(elements)}, "log": log}


# ---------------------------------------------------------------------------
# fixture 实现
# ---------------------------------------------------------------------------

def _write_state(root: Path, state: dict) -> None:
    (Path(root) / STATE_NAME).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_fixture(ctx: Ctx, loc: dict) -> tuple[str, str, dict]:
    ns = ctx.ns
    root = Path(ns.root).expanduser()
    notes = Path(ns.notes).expanduser().resolve()
    if not (notes / "bib_db" / "bibliography.jsonl").is_file():
        raise EnvProblem(f"真实 notes 仓里没有 bib_db/bibliography.jsonl：{notes}")
    if ns.teardown:
        state_file = root / STATE_NAME
        if not state_file.is_file():
            raise EnvProblem(f"找不到 fixture 状态文件：{state_file}（先跑一次 fixture，或 --root 给错了）")
        state = json.loads(state_file.read_text(encoding="utf-8"))
        before = snapshot_repo(Path(state["notes"]))
        plan = teardown_plan(root, state["domain"], state["key"], state.get("prior"))
        results = run_plan(plan)
        after_real = snapshot_repo(notes)
        same, diffs = snapshot_equal(state["real_snapshot"], after_real)
        readback_rc = results[1]["rc"]
        restored = (readback_rc != 0) if state.get("prior") is None else (
            results[1]["out"].strip() == state.get("prior"))
        log = [f"# fixture --teardown {_now()}", f"root={root}", f"prior={state.get('prior')!r}"]
        log += [f"$ {' '.join(r['argv'])}\n  rc={r['rc']} out={_clip(r['out'], 200)!r}" for r in results]
        log.append(f"真实 notes 未动: {same}（差异字段 {diffs or '无'}）")
        log.append(f"defaults 恢复: {restored}（prior={state.get('prior')!r}）")
        log.append(f"测试 clone 库 sha(清前): {before.get('db_sha256')}")
        text = "\n".join(log) + "\n"
        if not same:
            raise AssertError(f"真实 notes 仓被改动（字段 {diffs}）——立刻人工核查！",
                              {"fixture": {"root": str(root)}, "log": text})
        if not restored:
            raise AssertError("defaults 未恢复到原状（readback 不符）",
                              {"fixture": {"root": str(root)}, "log": text})
        return "PASS", "夹具已恢复并清理（真实库/真实 notes 未动）", {
            "fixture": {"mode": "teardown", "root": str(root), "restored": True,
                        "real_unchanged": True}, "log": text}

    real = Path(os.path.realpath(root))
    if real.exists() and any(real.iterdir()) and not ns.force:
        raise EnvProblem(f"夹具目录已存在且非空：{real}（先 --teardown，或 --force / 换 --root）")
    real_snapshot = snapshot_repo(notes)
    prior = None
    existing = run_cmd(["defaults", "read", ns.domain, ns.key])
    if existing.returncode == 0 and (existing.stdout or "").strip():
        prior = existing.stdout.strip()
    plan = fixture_plan(real, notes, ns.domain, ns.key)
    if ns.dry_run:
        text = (f"# fixture --dry-run {_now()}（不执行任何命令）\n"
                + "\n".join(f"$ {' '.join(s['argv'])}   # {s['note']}" for s in plan) + "\n")
        return "SKIP", "dry-run：只打印夹具计划", {"fixture": {"mode": "dry-run", "root": str(real),
                                                          "prior": prior}, "log": text}
    real.mkdir(parents=True, exist_ok=True)
    clone = real / "notes"
    state = {"root": str(real), "notes": str(clone), "domain": ns.domain, "key": ns.key,
             "prior": prior, "real_snapshot": real_snapshot, "created": _now(),
             "phase": "pending"}
    _write_state(real, state)          # 先落状态：即使中途崩，--teardown 也能恢复到原状
    results = run_plan(plan)
    readback = results[-1]["out"].strip()
    state["phase"] = "ready"
    _write_state(real, state)
    clone_db = sha256_file(clone / "bib_db" / "bibliography.jsonl")
    checks = {
        "readback_matches_clone": readback == str(clone),
        "clone_not_real_repo": os.path.realpath(clone) != os.path.realpath(notes),
        "clone_db_sha_matches_real": clone_db == real_snapshot.get("db_sha256"),
        "real_repo_sha_unchanged": True,
    }
    after_real = snapshot_repo(notes)
    same, diffs = snapshot_equal(real_snapshot, after_real)
    checks["real_repo_sha_unchanged"] = same
    log = [f"# fixture setup {_now()}", f"root={real}", f"notes(real)={notes}",
           f"prior={prior!r}", f"real_db_sha={real_snapshot.get('db_sha256')}",
           f"clone_db_sha={clone_db}"]
    log += [f"$ {' '.join(r['argv'])}\n  rc={r['rc']} out={_clip(r['out'], 200)!r}"
            + (f" err={_clip(r['err'], 200)!r}" if r['err'] else "") for r in results]
    log += [f"check {k}: {v}" for k, v in checks.items()]
    text = "\n".join(log) + "\n"
    bad = [k for k, v in checks.items() if not v]
    if bad:
        raise AssertError(f"夹具校验失败：{bad}", {"fixture": checks, "log": text})
    return "PASS", f"夹具就绪：{clone}（defaults {ns.domain} {ns.key} → 测试 clone）", {
        "fixture": {"mode": "setup", "root": str(real), "clone": str(clone),
                    "checks": checks, "prior": prior}, "log": text}


HANDLERS = {
    "select": cmd_select, "click": cmd_click, "field": cmd_field, "scroll": cmd_scroll,
    "shot": cmd_shot, "assert-text": cmd_assert_text, "assert-alert": cmd_assert_alert,
    "dump": cmd_dump, "fixture": cmd_fixture,
}


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# self-test（hermetic：期间 subprocess.run 被替换成哨兵）
# ---------------------------------------------------------------------------
