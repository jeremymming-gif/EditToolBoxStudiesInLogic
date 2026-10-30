#!/usr/bin/env python3
"""失败解释引擎（B-285a）：把「退出码 + 日志尾巴」翻成编辑能照着做的下一步。

**为什么要有这个模块**：编辑器/界面只拿到一个非 0 退出码和一段日志时，能说的往往只是
「失败了」。编辑要的不是失败，是「现在该做什么」——是稿子的问题、环境的问题，还是
工具自己的问题。这里把引擎里**真实存在的失败写法**（`❌ …` / 参数错误 / PermissionError /
xelatex 的 `!` 行 / biber 报错 / 文件被占用……）归成 17 类，每类固定给一句原因、
三条下一步和一个文档锚点。

**设计约束**（与 `Engine/docs/ENGINE_API.md` 的 `diagnose.v1` 契约一一对应）：

* **纯函数**：分类和报告都由入参决定；不读文件、不写文件、不联网。日志尾巴由 CLI 读好传进来。
* **不硬猜**：没有一条规则命中时就是 `unknown`，置信度 < 0.5，原因里直说「不硬猜」。
* **逐字节确定**：同一输入（含注入的 `generated_at`）→ 同一字节。不依赖当前时间、
  环境变量、文件系统顺序或哈希顺序。
* **不贴大段 traceback**：`reason` 与 `next_steps` 是给人看的两三句话；
  日志原文只进 `evidence.log_tail`（≤10 行）。

分类规则是**关键字/退出码驱动**的（`_RULES`），不是按夹具名硬编码——新增一种失败写法
时只加一条规则，四类报告与测试都不用动。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

#: 报告 schema 版本（写进 JSON；契约见 ENGINE_API.md §十一）
SCHEMA = "diagnose.v1"

#: 17 类（顺序即文档表顺序；`unknown` 必须是最后一个兜底）
CATEGORIES = (
    "usage",
    "source_missing",
    "source_type",
    "source_empty",
    "metadata_invalid",
    "template_unavailable",
    "output_conflict",
    "compile_tool_missing",
    "compile_tex_error",
    "compile_bib_error",
    "compile_no_pdf",
    "report_read_error",
    "permission_denied",
    "file_locked",
    "disk_full",
    "cancelled",
    "unknown",
)

#: 每类的严重度（`unknown`/`cancelled` 是警告：不是稿子/环境一定坏了）
SEVERITY = {
    "cancelled": "warning",
    "unknown": "warning",
}
DEFAULT_SEVERITY = "error"

#: 每类的一句话标题（给诊断卡抬头用；编辑扫一眼就知道是哪一类）
TITLES = {
    "usage": "命令行参数不对",
    "source_missing": "找不到源文件",
    "source_type": "源文件类型不支持",
    "source_empty": "源文件是空的",
    "metadata_invalid": "稿件基本信息读不进来",
    "template_unavailable": "排版模板不可用",
    "output_conflict": "输出文件冲突",
    "compile_tool_missing": "编译工具没装齐（xelatex / biber）",
    "compile_tex_error": "LaTeX 编译报错",
    "compile_bib_error": "参考文献编译报错",
    "compile_no_pdf": "编译结束了但没有 PDF",
    "report_read_error": "报告文件读不进来",
    "permission_denied": "没有权限写目标位置",
    "file_locked": "文件被别的程序占着",
    "disk_full": "磁盘空间不足",
    "cancelled": "操作被取消或中止",
    "unknown": "没认出是哪一类失败",
}

#: 下一步（每类固定三条；编辑照着做，不要求看代码）
NEXT_STEPS = {
    "usage": (
        "关掉工具重新打开再试一次（这类错误多半是启动参数不对）",
        "确认这次点的是正确的按钮/菜单项，中途没有改名或移动文件夹",
        "反复出现就把日志末尾几行发给维护者，说明刚刚点了什么",
    ),
    "source_missing": (
        "确认源文件还在原来的文件夹里——被移走、改名或删除都会这样",
        "在文件所在文件夹重新打开工具再跑一次，别用指向旧位置的快捷方式",
        "文件在网盘或移动硬盘上的，先等同步完成、确认磁盘已挂载",
    ),
    "source_type": (
        "确认选中的是 .tex 或 .docx 文件，不是 PDF、压缩包或中间产物",
        "Word 稿请选 .docx（老的 .doc 要先另存为 .docx）",
        "后缀看着对但还报错，检查文件名里有没有多余的点或空格",
    ),
    "source_empty": (
        "打开文件确认里面确实有内容（不是空模板、不是只剩注释）",
        "从备份或邮件附件重新拷一份完整的文件再试",
        "刚下载的文件要等下载完成再跑",
    ),
    "metadata_invalid": (
        "检查稿件的卷期年份、起止页码、中英文标题等基本信息是否填全",
        "刚编辑过信息表的，确认没有少引号、少逗号或多余换行",
        "拿同一期的另一篇稿件对比信息表格式",
    ),
    "template_unavailable": (
        "确认工具安装完整（重装一次最省事）",
        "检查模板文件夹有没有被杀毒软件隔离",
        "换一台机器试同一篇；也失败就把诊断发给维护者",
    ),
    "output_conflict": (
        "把冲突的输出文件改名或挪走再跑一次，别直接覆盖别人的成品",
        "确认输出文件不是只读、也没被其它程序打开",
        "换一个输出目录试一次；能跑通说明是目标路径的问题",
    ),
    "compile_tool_missing": (
        "这台机器缺少 TeX 环境：装完整版 TeX Live（2024 以上）后重启工具",
        "确认 xelatex、biber 在命令行里能直接运行（装了但没进 PATH 也算缺）",
        "换一台装好 TeX 的机器跑同一篇对比",
    ),
    "compile_tex_error": (
        "按日志里 `!` 开头的那一行去查稿件（常见：缺宏包、括号不配对、特殊字符）",
        "用单篇重跑定位；能编的篇目先跳过，不要一次改很多处",
        "确认这台机器的 TeX Live 是完整版、年份不太旧",
    ),
    "compile_bib_error": (
        "确认 .bib 文件存在、路径对，并且正文里确实引用了",
        "先手动跑一次 biber 看报错；数据库格式错误先修 .bib",
        "参考文献由联网工具生成的，先导出成 .bib 再重试",
    ),
    "compile_no_pdf": (
        "看同目录 .log 的末尾有没有 `!` 开头的错误行",
        "确认编译没有中途取消，输出目录有写权限",
        "单独重跑这一篇（编译全部会跳过已有 PDF，先删旧 PDF）",
    ),
    "report_read_error": (
        "报告文件可能被手改过或只写了一半：删掉后重跑那一步",
        "确认报告没有被其它程序占用（先关掉预览窗口）",
        "报告是从别的机器拷来的，检查拷贝是否完整",
    ),
    "permission_denied": (
        "把稿件文件夹移到「文档」等有写权限的位置，别放在系统目录",
        "确认文件没被设为只读（Windows 右键属性 / mac「显示简介」）",
        "关掉工具重开；公司电脑可请管理员放开该文件夹权限",
    ),
    "file_locked": (
        "关掉正在打开该文件的程序（PDF 阅读器、Word、网盘同步、杀毒扫描）",
        "等几秒再重试一次——扫描/同步通常很快就结束",
        "仍被占用就重启电脑再跑（最常见是资源管理器预览窗格）",
    ),
    "disk_full": (
        "清空回收站和下载文件夹，或换一个有空间的磁盘",
        "检查输出目录所在磁盘剩余空间（TeX 中间产物可能很大）",
        "把稿件和工具换到空间充足的盘再试",
    ),
    "cancelled": (
        "重新跑一次；如果每次都在同一位置取消，把操作顺序记下来",
        "不要在同一篇上反复点「取消」——先让上一次真正退出",
        "日志说步骤被中止的，检查是否有安全软件拦截",
    ),
    "unknown": (
        "重新跑一次，确认是否能复现",
        "把完整日志（不只末尾几行）发给维护者，附上入口名和退出码",
        "换一台机器或换一篇稿件试一次，缩小范围",
    ),
}

#: 17 类共用的文档锚点（真实标题见 ENGINE_API.md §十一）
DOC_ANCHOR = "ENGINE_API.md §十一 失败诊断（diagnose.v1）"

#: 日志只读尾部多少字节（D-06：大日志不整份读，界面也不整份贴）
LOG_TAIL_BYTES = 16 * 1024
#: 证据里最多放多少行日志（再长编辑也看不完）
LOG_TAIL_LINES = 10
#: artifacts 最多列多少个路径
ARTIFACTS_LIMIT = 10


@dataclass(frozen=True)
class _Rule:
    """一条分类规则：命中哪些日志写法、限哪些入口、权重多少（越大越优先）。"""

    category: str
    weight: int
    patterns: tuple[str, ...]
    entries: tuple[str, ...] = ()  # 空 = 任何入口都可能出现

    def __post_init__(self) -> None:
        assert self.category in CATEGORIES, self.category


# 顺序 = 同分时的优先顺序（更具体的在前）。权重是「这条写法有多能说明问题」，
# 不是分类优先级——`permission_denied` 的写法可能出现在任何入口，所以比 `compile_*` 高。
_RULES: tuple[_Rule, ...] = (
    _Rule("file_locked", 92, (
        r"WinError\s*32", r"另一个程序正在使用此文件", r"being used by another process",
        r"文件被占用", r"\bEBUSY\b", r"Resource temporarily unavailable",
        r"used by another process",
    )),
    _Rule("disk_full", 92, (
        r"No space left on device", r"Errno\s*28", r"\bENOSPC\b",
        r"磁盘空间不足", r"空间不足", r"disk (?:is )?full",
    )),
    _Rule("cancelled", 88, (
        r"已取消", r"取消操作", r"操作被取消", r"已中止", r"操作被中止",
        r"KeyboardInterrupt", r"SIGINT", r"\bCancelled\b", r"\bTerminated\b",
        r"被用户终止", r"aborted by user",
    )),
    _Rule("permission_denied", 84, (
        r"PermissionError", r"Permission denied", r"Errno\s*13", r"Access is denied",
        r"拒绝访问", r"没有权限", r"权限不足", r"Operation not permitted",
    )),
    _Rule("compile_tool_missing", 86, (
        r"找不到命令", r"command not found", r"不是内部或外部命令",
        r"未找到\s*xelatex", r"未找到\s*biber", r"xelatex[^\n]{0,40}not found",
        r"biber[^\n]{0,40}not found", r"请确认已安装", r"TeX Live 没有装",
        r"No such file or directory: '[^']*xelatex'",
    ), ("compileall.py", "convert.py", "publish.py")),
    _Rule("compile_bib_error", 78, (
        r"\bbiber\b", r"\bbibtex\b", r"I couldn't open database file",
        r"Biber error", r"\.bbl", r"参考文献(?:编译|处理)失败", r"run biber failed",
        r"Found no \\(?:citation|bibdata|bibstyle)",
    ), ("compileall.py", "convert.py", "publish.py")),
    _Rule("compile_tex_error", 74, (
        r"! LaTeX Error", r"! Undefined control sequence", r"! Package [^\n]*Error",
        r"! Missing [^\n]*", r"! Emergency stop", r"! Extra \}", r"! Argument of",
        r"xelatex 第", r"LaTeX 编译失败", r"l\.\d+", r"编译未通过",
    ), ("compileall.py", "convert.py", "publish.py")),
    _Rule("compile_no_pdf", 72, (
        r"未生成\s*PDF", r"没有生成\s*PDF", r"找不到[^\n]*\.pdf", r"no pdf",
        r"PDF (?:不存在|缺失|missing)", r"没有产出",
    ), ("compileall.py", "convert.py", "publish.py")),
    _Rule("template_unavailable", 68, (
        r"找不到模板", r"模板[^\n]{0,16}(?:不可用|缺失|不存在|损坏)",
        r"template[^\n]{0,20}(?:missing|not found)", r"\.sty[^\n]{0,20}(?:找不到|缺失|not found)",
        r"SLCN-MIX", r"样式文件[^\n]{0,16}(?:缺失|找不到)",
    ), ("convert.py", "compileall.py", "toc.py", "publish.py")),
    _Rule("source_empty", 70, (
        r"源文件是空", r"空文件", r"文件是空的", r"没有内容", r"0 字节",
        r"empty file", r"file is empty", r"size\s*[:=]\s*0\b",
    ), ("convert.py", "infocheck.py", "compileall.py", "mergepdf.py")),
    _Rule("source_type", 68, (
        r"不支持的(?:源)?文件类型", r"只支持[^\n]{0,20}(?:\.tex|\.docx)",
        r"文件类型[^\n]{0,12}(?:不支持|不对)", r"后缀[^\n]{0,12}(?:不支持|不对|应为)",
        r"扩展名[^\n]{0,12}(?:不支持|不对|应为)", r"unsupported file type",
        r"请选择[^\n]{0,12}\.docx",
    ), ("convert.py", "infocheck.py", "compileall.py", "mergepdf.py")),
    _Rule("source_missing", 64, (
        r"找不到源文件", r"源文件不存在", r"找不到文件", r"文件不存在",
        r"No such file or directory", r"File not found", r"cannot find",
        r"does not exist",
    ), ("convert.py", "infocheck.py", "compileall.py", "mergepdf.py", "checkbib.py")),
    _Rule("metadata_invalid", 66, (
        r"基本信息[^\n]{0,12}(?:读取|解析)失败", r"信息表[^\n]{0,12}(?:缺失|格式|解析)",
        r"metadata[^\n]{0,20}(?:invalid|missing|parse)", r"JSONDecodeError",
        r"Expecting value", r"json\.decoder", r"元数据[^\n]{0,12}(?:缺失|格式|解析)",
    ), ("convert.py", "bibdb.py", "toc.py", "convert_stage.py")),
    _Rule("output_conflict", 62, (
        r"输出目录[^\n]{0,16}(?:已存在|冲突)", r"拒绝覆盖", r"不覆盖已有",
        r"非本工具[^\n]{0,12}文件", r"原子替换[^\n]{0,12}(?:失败|冲突)",
        r"输出[^\n]{0,12}冲突", r"同名文件[^\n]{0,16}(?:已存在|冲突)",
        r"目标文件已存在",
    ), ("convert.py", "compileall.py", "mergepdf.py", "toc.py", "publish.py")),
    _Rule("report_read_error", 58, (
        r"报告[^\n]{0,12}(?:读取|解析|损坏)失败", r"报告文件[^\n]{0,16}(?:不存在|损坏|解析)",
        r"report[^\n]{0,20}(?:corrupt|invalid|unreadable)", r"JSONDecodeError",
        r"无法解析[^\n]{0,8}报告", r"报告格式[^\n]{0,12}(?:不对|不支持)",
    ), ("proofcheck.py", "infocheck.py", "compileall.py", "mergepdf.py", "toc.py",
        "publish.py", "checkbib.py")),
    _Rule("usage", 40, (
        r"usage:", r"用法[：:]", r"invalid choice", r"unrecognized arguments",
        r"the following arguments are required", r"参数[^\n]{0,12}(?:错误|不对|缺少|无效)",
        r"不认识的入口", r"未知(?:的)?入口", r"缺少[^\n]{0,8}参数", r"需要[^\n]{0,8}参数",
        r"命令[^\n]{0,8}(?:错误|不对)",
    )),
)

def _compiled() -> tuple[tuple[_Rule, tuple[re.Pattern[str], ...]], ...]:
    """规则里的正则只编译一次（模块级缓存；不缓存到全局变量，避免测试互相影响）。"""
    return tuple((rule, tuple(re.compile(p, re.IGNORECASE) for p in rule.patterns))
                 for rule in _RULES)


_COMPILED = _compiled()

#: 从日志里提取「提到过的产物路径」（证据 artifacts；只认常见的稿件/产物后缀）
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])?(?:[^\s\"'<>|，。；：、（）()\[\]]+[\\/])*[^\s\"'<>|，。；：、（）()\[\]]+"
    r"\.(?:tex|pdf|log|json|md|bib|sty|docx|zip|xlsx|txt)",
)


def _one_line(text: str, limit: int = 240) -> str:
    """压成一行（`reason` 里引日志行时用；不让换行把报告结构顶开）。"""
    s = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def split_log(log_text: str) -> list[str]:
    """日志文本 → 非空行列表（不去重、不改顺序；只去掉行尾空白）。"""
    return [ln.rstrip() for ln in log_text.splitlines() if ln.strip()]


def classify(entry: str, exit_code: int, log_text: str) -> tuple[str, float, list[str]]:
    """分类入口：返回 ``(category, confidence, matched_lines)``。

    * 退出码为 0 → `unknown`，置信度 0.1（诊断器是给失败用的；没有失败就不硬说原因）。
    * 进程被信号杀掉（负退出码 / 130 / 143）→ `cancelled`。
    * 否则逐条规则匹配，取「权重 + 命中行数」最高的一条；一条都不中 → `unknown`。
    """
    lines = split_log(log_text)
    if exit_code == 0:
        return "unknown", 0.1, []
    if exit_code < 0 or exit_code in (130, 143):
        return "cancelled", 0.66, lines[-3:]

    best: tuple[int, int, _Rule, list[str]] | None = None  # (score, -index, rule, matched)
    for idx, (rule, patterns) in enumerate(_COMPILED):
        if rule.entries and entry not in rule.entries:
            continue
        matched = [ln for ln in lines
                   if any(p.search(ln) for p in patterns)]
        if not matched:
            continue
        score = rule.weight + min(len(matched), 3) * 3
        if best is None or (score, -idx) > (best[0], best[1]):
            best = (score, -idx, rule, matched)
    if best is None:
        return "unknown", 0.3 if lines else 0.2, []
    _, _, rule, matched = best
    conf = 0.72 if rule.weight >= 80 else 0.66 if rule.weight >= 60 else 0.58
    if len(matched) >= 2:
        conf += 0.08
    if rule.entries:
        conf += 0.05
    return rule.category, min(0.95, round(conf, 2)), matched


def _reason(entry: str, exit_code: int, category: str, matched: list[str]) -> str:
    if exit_code == 0:
        return (f"入口 `{entry}` 的退出码是 0（这次没有失败）；"
                "如果界面仍显示异常，请把完整日志交给维护者。")
    if category == "unknown":
        return (f"入口 `{entry}` 以退出码 {exit_code} 结束；日志里没有匹配到已知的失败写法，"
                "所以不硬猜原因。")
    quote = f"，例如：{_one_line(matched[0], 160)}" if matched else ""
    return (f"入口 `{entry}` 以退出码 {exit_code} 结束；日志里有 {len(matched)} 行"
            f"与「{TITLES[category]}」的写法相符{quote}。")


def extract_artifacts(log_text: str) -> list[str]:
    """从日志里按出现顺序提取提到的文件路径（去重、最多 10 个）。"""
    out: list[str] = []
    for m in _PATH_RE.finditer(log_text):
        token = m.group(0).strip("，。；：、（）()[]\"'")
        if len(token) < 5 or token in out:
            continue
        out.append(token)
        if len(out) >= ARTIFACTS_LIMIT:
            break
    return out


def build(
    *,
    entry: str,
    exit_code: int,
    log_text: str,
    generated_at: str,
    engine_version: str | None = None,
) -> dict:
    """生成 `diagnose.v1` 报告（13 个契约键；键序即契约顺序）。

    `generated_at` 必须由调用方注入（CLI 取当前时间，测试注入固定值）——
    这是「同输入逐字节确定」能测的前提。
    """
    category, confidence, matched = classify(entry, int(exit_code), log_text)
    lines = split_log(log_text)
    version = engine_version if engine_version is not None else _engine_version()
    report = {
        "schema": SCHEMA,
        "entry": entry,
        "exit_code": int(exit_code),
        "category": category,
        "severity": "info" if int(exit_code) == 0 else SEVERITY.get(category, DEFAULT_SEVERITY),
        "title": TITLES[category],
        "reason": _reason(entry, int(exit_code), category, matched),
        "evidence": {
            "log_tail": lines[-LOG_TAIL_LINES:],
            "matched_lines": [_one_line(ln) for ln in matched[:LOG_TAIL_LINES]],
            "artifacts": extract_artifacts(log_text),
        },
        "next_steps": list(NEXT_STEPS[category]),
        "doc_anchor": DOC_ANCHOR,
        "confidence": confidence,
        "generated_at": generated_at,
        "engine_version": version,
    }
    return report


def render_json(report: dict) -> str:
    """单 JSON 对象（缩进 2；键序稳定，末尾一个换行）。"""
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def render_text(report: dict) -> str:
    """人类可读摘要（给终端/界面诊断卡；默认输出）。"""
    ev = report.get("evidence", {})
    out = [
        f"失败诊断：{report.get('title', '')}",
        (f"  入口：{report.get('entry', '')}　退出码：{report.get('exit_code', '')}"
         f"　类别：{report.get('category', '')}（置信度 {report.get('confidence', '')}）"),
        f"  原因：{report.get('reason', '')}",
    ]
    matched = ev.get("matched_lines") or []
    if matched:
        out.append(f"  日志里相中的行（{len(matched)} 条）：")
        out.extend(f"    · {ln}" for ln in matched)
    artifacts = ev.get("artifacts") or []
    if artifacts:
        out.append("  日志里提到的文件：")
        out.extend(f"    · {p}" for p in artifacts)
    out.append("  下一步：")
    for i, step in enumerate(report.get("next_steps") or [], 1):
        out.append(f"    {i}. {step}")
    out.append(f"  依据文档：{report.get('doc_anchor', '')}")
    tail = ev.get("log_tail") or []
    out.append(f"  日志末尾 {len(tail)} 行（只读，未写任何产物）：")
    out.extend(f"    {ln}" for ln in tail)
    out.append(f"  schema：{report.get('schema', '')}　引擎版本：{report.get('engine_version', '')}")
    return "\n".join(out) + "\n"


def _engine_version() -> str:
    """引擎版本号（唯一出处 `slcn/__init__.py`；延迟导入避免循环依赖）。"""
    try:
        from . import platform as _plat  # noqa: PLC0415
        return _plat.engine_version()
    except Exception:  # pragma: no cover - 只影响诊断报告里的版本行
        return "unknown"
