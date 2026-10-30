"""B-283：「环境检查 / 导出诊断包」的**纯逻辑**（解析 JSON / 显示行 / 诊断包内容）。

为什么不写在界面里：mac 的 `sources-mac/Core/EnvCheck.swift` 与 Windows 的设置页要显示
**同一份**结论，而「对等」必须能被 CI 验证 —— 本模块不 import tkinter、不 import engine，
于是 `sources-win/tests/test_bridge.py` 的 (l) 组在 mac / Linux / CI 上就能逐条断言，
不用开窗口、不用 Windows 真机。

判据只在引擎：`Engine/envcheck.py` 决定查什么、什么算 ok/warn/fail、退出码几；
这里只做三件事：把 `--json` 读回来、排成给人看的几行、把报告打成诊断包。
任何「缺哪一项该判失败」的逻辑都**不许**长在这份文件里。
"""
from __future__ import annotations

import json
import zipfile

#: 引擎 `envcheck.py --json` 的 schema 标识（不是这个值就当没读到）。
SCHEMA = "envcheck.v1"

#: 检查项固定顺序（与 `Engine/envcheck.py: CHECK_IDS` 同一份，桥接测试逐项比对）。
CHECK_IDS: tuple[str, ...] = (
    "engine_bundle",
    "xelatex",
    "biber",
    "libreoffice",
    "git",
    "disk_space",
    "output_writable",
    "template_bundle",
)

#: 结论记号：与 mac `EnvCheck.mark` 逐字一致。
MARKS = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
#: 级别文字：与 mac 侧同一份（「可选」缺了只是功能受限）。
LEVEL_TEXT = {"required": "必需", "optional": "可选", "info": "信息"}

#: 结果区还没有跑过检查时的占位（不假装有结论，也不吓人）。
PLACEHOLDER = "点「环境检查」看本机缺什么依赖（不联网、不改任何文件、不挡任何功能）。"

#: 诊断包里的两个文件（C5：解压出来必须有这两样，且**只有**这两样）。
DIAGNOSTICS_NAME = "diagnostics.txt"
REPORT_NAME = "envcheck.json"


def parse(text) -> dict:
    """从引擎输出里抠出 `envcheck.v1` 报告；读不到返回 `{}`（界面显示「没读到」而不是崩）。

    `engine.run_call` 把 stderr 合进了 stdout（致命错误要在同一个日志框里可见），
    所以这里按「第一个 `{` 到最后一个 `}`」取子串，再校验 schema —— 与 mac
    `EnvCheck.parse` 同一个口径。
    """
    if isinstance(text, (list, tuple)):
        text = "\n".join(str(x) for x in text)
    text = str(text or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return {}
    return payload


def check_rows(payload: dict) -> list[dict]:
    """规范化成界面用的行（不改变任何一个状态）。"""
    rows: list[dict] = []
    for item in (payload or {}).get("checks") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        level = str(item.get("level") or "")
        install = item.get("install") if isinstance(item.get("install"), dict) else {}
        rows.append({
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "level": level,
            "level_text": LEVEL_TEXT.get(level, level),
            "status": status,
            "mark": MARKS.get(status, "·"),
            "version": str(item.get("version") or ""),
            "detail": str(item.get("detail") or ""),
            "hint": str(item.get("hint") or ""),
            "install_windows": str(install.get("windows") or ""),
        })
    return rows


def body_lines(payload: dict) -> list[str]:
    """结果区正文：每项一行 + 缩进的说明；**只有缺项**才打安装提示（与人类报告同口径）。"""
    lines: list[str] = []
    for row in check_rows(payload):
        ver = f"  {row['version']}" if row["version"] else ""
        level = "" if row["level"] == "required" else f"（{row['level_text']}）"
        lines.append(f"{row['mark']} {row['title']}{level}{ver}")
        if row["detail"]:
            lines.append(f"    {row['detail']}")
        if row["status"] != "ok" and row["hint"]:
            lines.append(f"    → {row['hint']}")
    if not lines:
        lines.append("没读到检查结果（引擎输出不是 envcheck.v1 JSON）。")
    return lines


def summary_line(payload: dict) -> str:
    """一行小结：先讲结论，再报计数。退出码口径与引擎一致（1 = 可用但功能受限）。"""
    summary = (payload or {}).get("summary") or {}
    ok, warn, fail = (int(summary.get(k) or 0) for k in ("ok", "warn", "fail"))
    if not (payload or {}).get("checks"):
        return "没读到检查结果。"
    if fail:
        return f"❌ 有必需项缺失（{fail} 项）——按提示装好再重跑。"
    if warn:
        return f"⚠️ 必需项全部就绪；{warn} 项可选依赖缺失，相关功能受限。"
    return f"✅ 全部就绪（{ok} 项）。"


def missing_installs(payload: dict) -> list[tuple[str, str]]:
    """缺项 → (标题, Windows 安装命令)。界面做「复制命令」按钮时用。"""
    return [(row["title"], row["install_windows"])
            for row in check_rows(payload)
            if row["status"] != "ok" and row["install_windows"]]


def diagnostics_text(payload: dict, *, app_version: str, engine_version: str,
                     platform_text: str, run_mode: str,
                     generated_at: str = "") -> str:
    """诊断包里的 `diagnostics.txt`。

    ⚠️ **隐私纪律**：这里只拼「版本 / 系统 / 形态 / 检查结论」四类信息。
    不得写入稿件路径或标题、作者信息、API Key、引用库内容 —— 函数签名里就**没有**
    传这些的入口（`payload` 是 envcheck 报告，本身不含它们），以后也不许加。
    """
    lines = [
        "编辑部工具箱 · 环境诊断",
        f"生成时间：{generated_at or '（未记录）'}",
        f"App 版本：{app_version or '未知'}",
        f"引擎版本：{engine_version or '未知'}",
        f"操作系统：{platform_text or '未知'}",
        f"运行形态：{run_mode or '未知'}",
        "",
        "—— 检查结果 ——",
    ]
    lines.extend(body_lines(payload))
    lines += ["", summary_line(payload), "",
              "（本文件不含稿件内容、作者信息、API Key 或引用库内容。）"]
    return "\n".join(lines) + "\n"


def package_entries() -> tuple[str, str]:
    """诊断包内的文件清单（顺序 = 写入顺序）。只许两个 —— C5 的判据。"""
    return (DIAGNOSTICS_NAME, REPORT_NAME)


def write_package(zip_path: str, diagnostics: str, payload: dict) -> list[str]:
    """把诊断包写成 zip，返回包内文件名（供界面回显 / 测试断言）。"""
    diagnostics_name, report_name = package_entries()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(diagnostics_name, diagnostics)
        zf.writestr(report_name, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return [diagnostics_name, report_name]


def done_text(done: bool) -> str:
    """「设置」页按钮旁的状态说明（不拦任何操作）。"""
    return ("上次检查过。缺项不挡任何功能，装好依赖后点「环境检查」重跑即可。"
            if done else "还没检查过。点「环境检查」看本机缺什么依赖。")
