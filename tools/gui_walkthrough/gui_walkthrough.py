#!/usr/bin/env python3
"""gui_walkthrough.py —— macOS GUI「真点走查」工具入口（B-191 T1；公开仓 tools/gui_walkthrough/）

定位：**替换 archived `gui_fast_test.py`（B-85）的角色** —— 那个脚本是「初排转换」专用批处理驱动，
不是通用真点走查；本工具补的是「按名字定位 → 点/填/滚/截图/断言 → evidence pack」这条走查链，
把 B-188/B-190 两次手工走查（50–80 步、2 次 alert 按索引误点、证据命名手写）压到 5–8 步。

快速上手（细节见本目录 README.md）：
    python3 tools/gui_walkthrough/gui_walkthrough.py --self-test     # hermetic 自测（≥12 例）
    python3 tools/gui_walkthrough/gui_walkthrough.py --help
    # 一次真点走查（命令可分开跑，evidence 自动续编号）：
    gui_walkthrough.py fixture --root /tmp/gw-run                    # /tmp 夹具 + defaults 指向
    gui_walkthrough.py select --role AXOutlineRow --name 待处理
    gui_walkthrough.py assert-text --contains "库 sha"
    gui_walkthrough.py shot --slug 待处理页
    gui_walkthrough.py fixture --teardown                            # 恢复 defaults + 清理 + 校验
    cat $TMPDIR/gui-walkthrough-evidence/VERDICT-SUMMARY.txt

9 个子命令：select / click / field / scroll / shot / assert-text / assert-alert / dump / fixture。
退出码：0 成功 · 1 断言失败 · 2 用法错误 · 3 缺辅助功能权限 · 4 环境问题。
分层：`gwcore.py`（纯函数/evidence/fixture 计划）、`gwdriver.py`（Ctx + 子命令）、`gwselftest.py`（自测）、
本文件（命令行入口）。底层只用系统能力：`swiftc` 编译的 `axhelper.swift`（AXUIElement）+ osascript +
pbcopy + screencapture；零第三方运行时依赖、零联网。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gwcore import (  # noqa: E402
    ALL_COMMANDS, DEFAULT_APP, DEFAULT_DOMAIN, DEFAULT_EVIDENCE_DIR, DEFAULT_KEY,
    DEFAULT_NOTES_REPO, DEFAULT_TIMEOUT, EXIT_ENV, EXIT_NO_PERM, EXIT_OK, EXIT_USAGE,
    TOOL_VERSION, AssertError, EnvProblem, PermissionProblem, ToolError, UsageError,
    _now, default_slug, emit, locator_from_ns, slugify,
)
from gwdriver import Ctx, HANDLERS  # noqa: E402
from gwselftest import self_test  # noqa: E402


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

# 公共选项的默认值：父解析器用 SUPPRESS 注入（子命令里没给时不会覆盖主解析器的值），
# 未出现时由 normalize_common() 补默认 —— 这样 `--json` 写在子命令前后都生效。
COMMON_DEFAULTS = {
    "evidence_dir": str(DEFAULT_EVIDENCE_DIR),
    "json": False,
    "reset_evidence": False,
    "pid": None,
    "app": str(DEFAULT_APP),
    "process_name": "",
    "helper": "",
    "slug": "",
    "frontmost": False,
}


def _common_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    s = argparse.SUPPRESS
    p.add_argument("--evidence-dir", default=s, metavar="DIR",
                   help=f"evidence pack 目录：**只接受绝对路径**（相对路径/`.` → exit 2，防写进 cwd 污染工作树）；"
                        f"留空 = 默认 {DEFAULT_EVIDENCE_DIR}（建议显式传绝对路径）")
    p.add_argument("--json", action="store_true", default=s, help="stdout 输出机器可读 JSON")
    p.add_argument("--reset-evidence", action="store_true", default=s,
                   help="把该目录旧证据归档到 archive-<ts>/ 后重新从 01 编号")
    p.add_argument("--pid", type=int, default=s, help="目标 App 的 pid（默认按 --app 自动找）")
    p.add_argument("--app", default=s, metavar="PATH",
                   help=f"App bundle 路径（默认 {DEFAULT_APP}）")
    p.add_argument("--process-name", default=s, metavar="NAME",
                   help="AppleScript 用的进程名（默认从 bundle 的 Contents/MacOS/ 推）")
    p.add_argument("--helper", default=s, metavar="PATH", help="已编译 axhelper 路径（跳过编译）")
    p.add_argument("--slug", default=s, help="证据文件名里的名称（默认由目标名生成）")
    p.add_argument("--frontmost", action="store_true", default=s,
                   help="动作前先把 App 切到前台（AppleScript；部分 App 不前置时选中不生效）")
    return p


def _locator_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--path", default="", help="AX 数字路径（如 0.6.3；与名称定位二选一）")
    p.add_argument("--role", default="", help="角色（如 AXButton / AXOutlineRow；AX 前缀为精确，否则子串）")
    p.add_argument("--name", default="", help="名字（title/desc/value/help 任一命中；默认子串）")
    p.add_argument("--title", default="", help="按 title 属性过滤")
    p.add_argument("--desc", default="", help="按 description 属性过滤")
    p.add_argument("--help-text", default="", help="按 help 属性过滤")
    p.add_argument("--within", default="", help="只在某个 AX 子树内找（数字路径）")
    p.add_argument("--nth", type=int, default=None, help="多命中时显式取第 N 个（默认多命中即拒绝）")
    p.add_argument("--exact", action="store_true", help="名称/标题精确匹配（默认子串）")
    p.add_argument("--subtree", action="store_true",
                   help="名字也匹配子树（行/单元格的文字常在子节点；自动只取最深命中）")
    p.add_argument("--max-depth", type=int, default=0, help="搜索/遍历深度上限（0=默认）")
    return p


def build_parser() -> argparse.ArgumentParser:
    common, locp = _common_parent(), _locator_parent()
    ap = argparse.ArgumentParser(
        prog="gui_walkthrough.py",
        description="macOS GUI 真点走查（AX 定位 + 点击/填值/滚动/截图/断言 + evidence pack）",
        epilog=("子命令: " + " / ".join(ALL_COMMANDS) + "\n"
                "退出码: 0 成功 · 1 断言失败/目标不存在 · 2 用法错误 · 3 缺辅助功能权限 · 4 环境问题\n"
                "证据: 每步 NN-<cmd>-<slug>.log|.json|.png + VERDICT-SUMMARY.txt/.json（固定命名）\n"
                "alert: assert-alert 只按标题 + 按钮名定位/点击（禁索引；B-190 曾按索引误点 2 次）\n"
                "settle: click 前等 UI settle（≈1.5–2s 或等刷新完成）—— 页面异步刷新会让目标漂移；"
                "工具按 drift 保护拒绝误点、不做自动重试\n"
                "fixture: 只在 /tmp 建 bare clone + clone，defaults 指向测试 clone；--teardown 恢复并校验"),
        formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    ap.add_argument("--version", action="version", version=f"gui_walkthrough {TOOL_VERSION}")
    ap.add_argument("--self-test", action="store_true",
                    help="hermetic 自测（不起 App / 不读真实库 / 不联网 / 不写真实 defaults）")
    sub = ap.add_subparsers(dest="cmd", metavar="<子命令>")

    def _add(name, help_text, *, with_loc=True):
        return sub.add_parser(name, help=help_text, description=help_text,
                              parents=[common, locp] if with_loc else [common],
                              formatter_class=argparse.RawDescriptionHelpFormatter)

    p = _add("select", "按 AX 路径/角色+名称定位并 focus/selected（多命中拒绝）")
    p.add_argument("--fallback-press", action="store_true", help="focus/selected 未生效时退化为 AXPress")
    p.add_argument("--allow-unverified", action="store_true", help="未验证生效也算 PASS（不推荐）")

    _add("click", "AXPress 点击目标（按下前按名字复核；页面异步刷新时先等 UI settle ≈1.5–2s，工具不自动重试）")

    p = _add("field", "写文本字段（CJK 走 pbcopy+⌘A/⌘V；ASCII 先试 AX 写入并 readback）")
    p.add_argument("--text", default=None, help="要写入的文本（可含 CJK/换行）")
    p.add_argument("--text-file", default="", help="从文件读文本（替代 --text）")
    p.add_argument("--method", choices=("auto", "ax", "paste"), default="auto",
                   help="auto（默认：非 ASCII 直接粘贴，否则先 AX 写入）/ ax / paste")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="readback 轮询超时秒")

    p = _add("scroll", "设 scrollbar 位置（--value 0..1 或 --delta 相对）")
    p.add_argument("--value", type=float, default=None, help="目标位置 0..1")
    p.add_argument("--delta", type=float, default=None, help="相对位移（如 0.5 往下半页）")
    p.add_argument("--allow-unverified", action="store_true", help="未验证生效也算 PASS（不推荐）")

    p = _add("shot", "截图（默认窗口 rect 来自 AX frame；--full 整屏）")
    p.add_argument("--full", action="store_true", help="整屏截图（不需要 AX）")
    p.add_argument("--pad", type=int, default=0, help="窗口矩形外扩像素（默认 0）")

    p = _add("assert-text", "断言 AX 文本包含/等于（轮询到 --timeout；失败 exit 1）")
    p.add_argument("--contains", default=None, help="包含该文本即 PASS")
    p.add_argument("--equals", default=None, help="有元素文本完全相等才 PASS")
    p.add_argument("--ignore-case", action="store_true", help="忽略大小写")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="轮询超时秒（默认 5）")
    p.add_argument("--optional", action="store_true", help="失败记 SKIP 且 exit 0（显式声明，默认关）")

    p = _add("assert-alert", "按 alert 标题 + 按钮名断言/点击（禁索引）", with_loc=False)
    p.add_argument("--title", default="", help="alert 标题（必填；按文本/窗口标题匹配）")
    p.add_argument("--body", default="", help="alert 正文再断言一次（可选）")
    p.add_argument("--button", default="", help="按钮名（默认精确匹配）")
    p.add_argument("--button-contains", action="store_true", help="按钮名用子串匹配（默认精确）")
    p.add_argument("--click", action="store_true", help="点击该按钮（必须同时给 --button）")
    p.add_argument("--index", type=int, default=None, help="（禁用）按索引点按钮；给了就 exit 2")
    p.add_argument("--exact-title", action="store_true", help="标题精确匹配（默认子串）")
    p.add_argument("--ignore-case", action="store_true", help="标题/正文忽略大小写")
    p.add_argument("--max-depth", type=int, default=8, help="找 alert 的遍历深度（默认 8）")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="等 alert 出现的超时秒")
    p.add_argument("--dismiss-timeout", type=float, default=3.0, help="点完等消失的超时秒")
    p.add_argument("--no-verify-dismiss", action="store_true", help="不校验点击后 alert 消失")
    p.add_argument("--optional", action="store_true", help="失败记 SKIP 且 exit 0")

    p = _add("dump", "dump AX 树（限深；写 evidence .log + .json）")
    p.add_argument("--contains", default=None, help="只留名字含该文本的元素（--name 的别名）")
    p.add_argument("--print", action="store_true", help="把树直接打到 stdout（默认只报数量）")

    p = _add("fixture", "隔离夹具：/tmp bare clone notes + clone + defaults 指向；--teardown 恢复",
             with_loc=False)
    p.add_argument("--root", default=str(Path(tempfile.gettempdir()) / "gui-walkthrough-fixture"),
                   help="夹具根（必须在 /tmp 或 $TMPDIR 下）")
    p.add_argument("--notes", default=str(DEFAULT_NOTES_REPO), help="真实 notes 仓（只读 clone 源）")
    p.add_argument("--domain", default=DEFAULT_DOMAIN, help="App defaults domain")
    p.add_argument("--key", default=DEFAULT_KEY, help="App defaults key")
    p.add_argument("--teardown", action="store_true", help="恢复 defaults + 清理 + 校验真实库未动")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    p.add_argument("--force", action="store_true", help="夹具目录非空也继续（危险，慎用）")
    return ap


def normalize_common(ns) -> None:
    for key, value in COMMON_DEFAULTS.items():
        if not hasattr(ns, key):
            setattr(ns, key, value)
    if not str(getattr(ns, "evidence_dir", "") or "").strip():
        ns.evidence_dir = COMMON_DEFAULTS["evidence_dir"]   # `--evidence-dir ""` 别写到 cwd


def _slug_for(ns) -> str:
    return slugify(ns.slug or "", fallback=default_slug(ns, ns.cmd))


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def dispatch(ns) -> int:
    """执行一个子命令：统一 evidence 记录 + 输出 + 退出码。"""
    if ns.cmd not in HANDLERS:
        raise UsageError(f"未知子命令：{ns.cmd}")
    ctx = Ctx(ns)
    t0 = time.monotonic()
    slug = _slug_for(ns)
    handler = HANDLERS[ns.cmd]
    loc = locator_from_ns(ns) if hasattr(ns, "path") else {}
    try:
        status, message, data = handler(ctx, loc)
        code = EXIT_OK
    except AssertError as exc:
        if getattr(ns, "optional", False):
            status, message, data, code = "SKIP", f"（--optional）{exc}", exc.data, EXIT_OK
        else:
            status, message, data, code = "FAIL", str(exc), exc.data, exc.code
    except (UsageError, PermissionProblem, EnvProblem) as exc:
        status, message, data, code = exc.status, str(exc), exc.data, exc.code
    except ToolError as exc:  # pragma: no cover - 兜底
        status, message, data, code = exc.status, str(exc), exc.data, exc.code
    ms = (time.monotonic() - t0) * 1000
    text_log = data.get("log", "")
    binary = data.get("_binary")
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    if status == "FAIL" and code in (EXIT_USAGE, EXIT_NO_PERM, EXIT_ENV) and not text_log:
        text_log = f"# {ns.cmd} {_now()}\n错误（exit {code}）：{message}\n"
    if binary and status != "PASS":
        Path(binary[1]).unlink(missing_ok=True)
        binary = None
    entry = ctx.evidence.record(ns.cmd, slug, status, ms=ms, note=message[:160],
                                text_log=text_log, json_data=payload, binary=binary)
    if binary:
        Path(binary[1]).unlink(missing_ok=True)
    entry["_dir"] = str(ctx.evidence.dir)
    if code == EXIT_ENV:
        print(f"✗ 环境问题（exit 4）：{message}", file=sys.stderr)
    return emit(ns, entry, status, message, data, code=code)


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    ap = build_parser()
    ns = ap.parse_args(argv)
    normalize_common(ns)
    if getattr(ns, "self_test", False):
        return self_test()
    if not getattr(ns, "cmd", None):
        ap.print_help()
        return EXIT_USAGE
    try:
        return dispatch(ns)
    except UsageError as exc:            # 例如 --evidence-dir 相对路径：用法错 → exit 2（不建证据目录）
        print(f"✗ 用法错误（exit {EXIT_USAGE}）：{exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("✗ gui_walkthrough：被中断", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 —— 内部错误不静默
        import traceback
        traceback.print_exc()
        print(f"✗ gui_walkthrough：内部错误 {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ENV


if __name__ == "__main__":
    raise SystemExit(main())
