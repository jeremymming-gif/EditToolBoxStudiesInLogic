#!/usr/bin/env python3
r"""批量实例测试驱动 `tools/batch_case_test.py`（Phase1，2026-10-18，case-lab）。

**为什么需要**：期 87 / 期 90 的真实稿实例测试此前靠手工拼命令（批 36–38 的证据脚本只活在
`/tmp/slcn87/analyze.py`、`/tmp/verify87/run_all.py`，随时会被清理）。本脚本把「转换 → 编译 →
两遍 sha256 确定性 → 一校核查 → 文献回读校验 → 与稿件仓人工成品对照 → P0/P1/P2 分级报告」
串成一条命令，**零付费、只读输入、产物只写 /tmp**。

**纪律（写死在本脚本里）**：
1. **零付费**：只用 `convert.py --verify-plan --search-refs`（只算账：不生成任务书、不检索、
   不调用 AI），启动时清空 `SLCN_LLM_*` / `SLCN_SEARCH_*` / `*_API_KEY` 等付费相关环境变量，
   日志必须出现「预计 AI：0 次」，否则该案例判 P0。**绝不**传 `--verify-refs` / `--llm-*`。
2. **只读输入**：Desktop 工作区与稿件仓（人工成品）只读；`--out` 默认 `/tmp/batch_case_test/<run>`，
   绝不写回 Desktop；不刷新用户 `testResult`。
3. **确定性**：同一产物目录连跑两遍（pass1 = 转换+编译；pass2 = `--verify-plan` 再转换一次，
   产物照常写出），比较 `<Author>.tex` / `reference.bib` 的 sha256；不一致判 P0。
4. **人工对照**：按内置 manifest 找 `稿件仓/20xx年第N期(NN)/0N <Author>/`，只读计算页数/
   文献数/正文引用数/节结构/引用闭合/一校命中，**生成 vs 人工**并列进报告。

用法（在公开仓根）：

    PYTHONPATH=Engine/vendor /opt/homebrew/bin/python3.14 tools/batch_case_test.py \
        --out /tmp/batch_case_test/baseline-v1.2.1

    # 只跑期 90 十篇（风险前置）；期 87 九篇后跑
    ... --issues 90 --out /tmp/batch_case_test/run90

    # 单案例（05 Beibei_Gao 精确回归）
    ... --case 90/05 --out /tmp/batch_case_test/beibei

    # 关闭 manifest 附加源（模拟「只靠引擎通用发现」的 A2-only 口径；期 90/03 对照用）
    ... --issues 90 --no-metadata --out /tmp/batch_case_test/a2-only

    # 快速自检（不读真实稿、不跑 xelatex，秒级）
    ... --self-test

输出（`--out` 下）：

    report.md                 主报告（结论 / 零付费与确定性证据 / 总表 / P0·P1·P2 明细 /
                              人工对照 / 已知缺口 / 每案例附录 / 复现命令）
    summary.json              结构化数据（每案例 metrics + findings + totals）
    products/<Author>/        产物（.tex / reference.bib / .pdf / 日志 / 中间件 / .bak）
    logs/convert/<key>_pass*.log   转换/编译/算账原始日志（含命令行）
    logs/proof/<key>_gen.md、<key>_man.md   一校核查报告（生成稿/人工成品）
    logs/bib/<key>.md         文献回读校验报告

退出码：`0` 全部案例跑完（发现数见报告，**不代表没有问题**）；`1` 脚本/环境错误；
`2` 用法错误。本脚本**不判断**「有没有 P0 就算失败」——基线阶段已知缺口本来就存在，
由调用方读 `summary.json` 的 `totals`。

manifest（`--manifest file.json`，可选；缺省用内置期 87/90 清单，源文件自动发现）：

    {"cases": [
      {"key": "88-01", "issue": "88", "author": "Tuomo_Aho",
       "author_dir": "Tuomo_Aho", "workspace": "/abs/path/macCaseTest88",
       "source": "Tuomo_Aho/xxx.docx",        # 省略=在工作区按规则自动发现
       "metadata": "yalcin puzzle.tex",       # 可选：附加源（convert.py --metadata）
       "manual_dir": "01 Tuomo_Aho",          # 相对稿件仓期目录；省略=不做人工对照
       "manual_bib": "reference.bib", "extra_args": []}
    ]}

**附加源（`metadata`）判据**：LaTeX 多文件稿常把「正文片段」和「模板/宏定义」分成两个 `.tex`
（如期 90/03：`main.tex` 是纯正文片段，作者宏 `\\val/\\e/\\A` 定义在同目录 `yalcin puzzle.tex:224`；
`convert.py` 的标准用法是 `--source main.tex --metadata "yalcin puzzle.tex"`）。manifest 的
`metadata` 字段就是给这类稿准备的：**非空** → 命令追加 `--metadata <解析到的绝对路径>`；
解析顺序 = 绝对路径直接用 → 工作区根 → 作者目录 → 源文件所在目录；
**文件不存在 → 安全回退**：不传 `--metadata`、在 metrics/报告里写醒目 note（不阻断案例、不静默）；
`extra_args` 里已自带 `--metadata` 时不重复追加。字段为空 = 单源（默认行为不变）。

判据与分级（可单测的纯函数 `grade_case()`）：

* **P0**（内容/可用性硬失败）：转换 rc≠0、无 PDF、编译 `!` 错误、undefined citation/reference、
  `Missing character`>0、两遍 sha256 不一致、零付费断言失败、悬空引用>0、生成正文 0 引用但
  bib>0、biber ERROR、`.doc→docx` 失败、页数/文献数与人工差超阈值。
* **P1**（体例/结构）：页数差 1、文献数差 1–2、正文引用数差>10%、占位符>0、节结构差≥1、
  一校「可自动改」残留、未引用条目比人工多>5、必补字段比人工多>10。
* **P2**（增强/提示）：未引用条目小幅多出、中文旁半角短杠（ZH-08）比人工多等。

已知缺口（Phase0 盘点 + 案例 141–144 登记）写在 `KNOWN` 里，报告里标「已知」，不吞掉。

本脚本只写 /tmp 并输出报告；不写三仓、不 commit/push、不 spawn 子智能体。
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 仓库 / 引擎定位（脚本在 <repo>/tools/ 下）
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "Engine"
VENDOR = ENGINE / "vendor"
for _p in (str(ENGINE), str(VENDOR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # 引擎（唯一一份文档逻辑）：判据尽量复用，不另写一套
    from slcn import __version__ as ENGINE_VERSION
    from slcn import score as _score
    from slcn import sources as _sources
    from slcn.bibcheck import check as _bib_check
    from slcn.bibcheck import cited_keys as _cited_keys
    from slcn.bibcheck import entry_keys as _entry_keys
    from slcn.bibnorm import split_entries as _split_bib_entries
except Exception as _exc:  # pragma: no cover - 环境缺失时给出可操作提示
    raise SystemExit(
        f"无法导入引擎（需要 {ENGINE} 与 {VENDOR}）：{_exc}\n"
        "请在公开仓根运行：PYTHONPATH=Engine/vendor /opt/homebrew/bin/python3.14 "
        "tools/batch_case_test.py --self-test"
    )

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    try:
        from PyPDF2 import PdfReader  # type: ignore[no-redef]
    except Exception:  # pragma: no cover
        PdfReader = None  # type: ignore[assignment]

HOME = Path.home()
DEFAULT_OUT_ROOT = Path("/tmp/batch_case_test")
DEFAULT_WORKSPACES = {
    "90": HOME / "Desktop" / "macCaseTest90",   # 用户 2026-10-18 迁移（旧 macCaseTest 已不存在）
    "87": HOME / "Desktop" / "macCaseTest87",
    "88": HOME / "Desktop" / "macCaseTest88",   # 7 篇英文（Word/LaTeX 混合）
    "89": HOME / "Desktop" / "macCaseTest89",   # 5 篇 LaTeX 子集
    "86": HOME / "Desktop" / "macCaseTest86",   # 10 篇（第四十四批新增；Word/LaTeX 混合）
}
MANUSCRIPTS_DEFAULT = ROOT.parent / "Studies_in_Logic_Database"
MANUAL_ROOTS = {
    "90": "2026年第5期(90)",
    "87": "2026年第2期(87)",
    "88": "2026年第3期(88)",
    "89": "2026年第4期(89)",
    "86": "2026年第1期(86)",
}
DEFAULT_ISSUE_ORDER = ("90", "87", "88", "89", "86", "repo")  # 已知工作区先跑，repo 扫描最后

# O10（第四十四批）：相似度性能阈值——ref+gen 超过该字符数时降级为行级/前缀快速指标
SIM_FULL_MAX_CHARS = 30000
SIM_FAST_CAP = 20000

# 全量基准（task-40）：repo 扫描时跳过 Desktop 已覆盖的期（避免重复跑）
REPO_SKIP_ISSUE_CODES = frozenset({"87", "88", "89", "90"})
# 估算用：单案例「转换+编译+算账+一校/回读+人工对照」的经验耗时（秒；由 19 篇实测 ≈7.3s 放大）
BENCH_EST_SEC_PER_CASE = 8.5

# 付费相关环境变量：启动即清空（前缀 + 显式名单）
PAID_ENV_PREFIXES = ("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_AI_")
PAID_ENV_NAMES = frozenset(
    {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"}
)

# ─────────────────────────────────────────────────────────────────────────────
# 判据阈值（单测直接引用；改口径只改这里）
# ─────────────────────────────────────────────────────────────────────────────

PAGE_DIFF_P1 = 1        # 页数差 1 → P1
PAGE_DIFF_P0 = 2        # 页数差 >=2 → P0（未登记原因时）
BIB_DIFF_P1_MAX = 2     # 文献条数差 1–2 → P1
BIB_DIFF_P0_MIN = 3     # 文献条数差 >=3 → P0（未登记原因时）
CITE_REL_DIFF = 0.10    # 正文引用数相对差 >10% → P1
UNUSED_EXCESS_P1 = 5    # 未引用条目比人工多 >5 → P1；多 1–5 → P2
BIB_MUST_EXCESS = 10    # 必补字段数比人工多 >10 → P1

# ─────────────────────────────────────────────────────────────────────────────
# 内置 manifest：期 90 十篇（先）→ 期 87 子案例 1–9（后）
#   source 留空 = 在工作区 <author_dir>/ 里按规则自动发现主文件（唯一 .tex / main.tex /
#   排除「作者信息/英文摘要/摘要/abstract/协议」后的最大 Word 文件）。这样不必手抄长文件名，
#   但子案例 → 作者名 / 人工成品目录的映射是显式的（Phase0 盘点结论）。
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CaseSpec:
    key: str
    issue: str
    author: str
    author_dir: str
    manual_dir: str = ""
    manual_bib: str = "reference.bib"
    source: str = ""                     # 相对工作区（或绝对路径）；空 = 自动发现
    metadata: str = ""                   # 附加源/元数据源（convert.py --metadata）；空 = 单源
    workspace: str = ""                  # 绝对路径：覆盖 issue 级工作区（repo 扫描案例用）
    manual_root: str = ""                # 绝对路径：覆盖 issue 级人工成品根（repo 扫描案例用）
    extra_args: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "key": self.key, "issue": self.issue, "author": self.author,
            "author_dir": self.author_dir, "manual_dir": self.manual_dir,
            "manual_bib": self.manual_bib, "source": self.source,
            "metadata": self.metadata, "workspace": self.workspace,
            "manual_root": self.manual_root,
            "extra_args": list(self.extra_args),
        }


def default_cases() -> list[CaseSpec]:
    """内置清单：期 90（10 篇）在前、期 87 子案例 1–9 在后。"""
    cases: list[CaseSpec] = []
    for no, author in (
        ("01", "Jingru_Kou"), ("02", "Zixin_Luo"), ("03", "Liang_Zhang"),
        ("04", "Haiting_Huang"), ("05", "Beibei_Gao"), ("06", "Yingying_Tang"),
        ("07", "Liangying_Zhao"), ("08", "Yanlin_Liao"), ("09", "Jiawei_Chu"),
        ("10", "Bo_Chen"),
    ):
        extra = {"metadata": "yalcin puzzle.tex"} if no == "03" else {}
        cases.append(CaseSpec(key=f"90-{no}", issue="90", author=author,
                              author_dir=author, manual_dir=f"{no} {author}", **extra))
    for sub, (no, author) in enumerate((
        ("02", "Kun_Xing"), ("03", "Wenjing_Du"), ("04", "Fayang_Ye"),
        ("05", "Pengfei_Song"), ("06", "Ruixue_Tang"), ("07", "Guangwu_Feng"),
        ("08", "Ran_Ren"), ("09", "Runcheng_Liang"), ("10", "Wei_Chen"),
    ), start=1):
        manual_bib = "reference.bib"
        if author in ("Ruixue_Tang", "Runcheng_Liang"):
            manual_bib = f"{author}.bib"   # 期 87 人工成品里这两份 bib 用作者名
        cases.append(CaseSpec(key=f"87-{sub}", issue="87", author=author,
                              author_dir=str(sub), manual_dir=f"{no} {author}",
                              manual_bib=manual_bib))
    # 期 88（7 篇英文：Word/LaTeX 混合；源文件来自历史用例，映射见 /tmp/case88_TEST_REPORT.md）
    for no, author, source in (
        ("01", "Tuomo_Aho", "original/CONSEQ2026FINAL.docx"),
        ("02", "Lu_Jiang", "lujiang/Ockham Semantics Submission Studies in Logic revised version May 25 2026 source file.docx"),
        ("03", "Zhentao_Zhang", "origin/main.tex"),
        ("04", "Chen_Liu", "Chenliu/SLEN_template.tex"),
        ("05", "YouAn_Su", "youansu/main.tex"),
        ("06", "Zhixi_Chen", "zhixichen/Revised Manuscript 5.23.docx"),
        ("07", "Hu_Liu", "huliu/Approved+2025+Williamson+Interview.docx"),
    ):
        cases.append(CaseSpec(key=f"88-{no}", issue="88", author=author,
                              author_dir=author, manual_dir=f"{no} {author}",
                              source=source))
    # 期 89（5 篇 LaTeX 子集；多文件稿由引擎 A2 自动配前言源）
    for no, author, source in (
        ("01", "Fenrong_Liu", "Fenrong_Liu/main_CN.tex"),
        ("02", "Xiaoxuan_Fu", "偏好逻辑特刊/SLCN_template.tex"),
        ("05", "Zhen_Liang", "original/社群动态偏好逻辑的公理化研究（修改版）.tex"),
        ("06", "Dazhu_Li", "层级选举模型中的偏好聚合及其规范性质分析/submission-final submssion.tex"),
        ("07", "Yiyan_Wang", "(Latex版)_逻辑学研究_从个体偏好到集体理性__revision_1_/WANGYIYAN.tex"),
    ):
        cases.append(CaseSpec(key=f"89-{no}", issue="89", author=author,
                              author_dir=author, manual_dir=f"{no} {author}",
                              source=source))
    # 期 86（第四十四批新增；Desktop macCaseTest86 子案例 1..10 ↔ 稿件仓 01..10，
    # 映射按人工成品 PDF 标题核对；4/5 为 LaTeX，5 带 SLCN.sty+bib）
    for no, author, source in (
        ("01", "Yu_Chen", "1/广义论证理论视域下视频公益广告的多模态说服传播【修改稿3】.docx"),
        ("02", "Han_Mei", "2/以广义论证为视角解读《孙子兵法》战略三篇论证 - 洁净修改稿.docx"),
        ("03", "Xiaohua_Wu", "3/(修改定稿1）广义论证视域下苗族“蒙脸”占卜仪式的说理研究——基于“问亡灵”仪轨的“细描”.docx"),
        ("04", "Zuojun_Xiong", "4/SL-Logic of Secrets and Exclusive Knowledge .tex"),
        ("05", "WeiNong_Li", "5/qiudejun0724.tex"),
        ("06", "Zhenzhong_Zhou", "6/真理论的目标、结构与方法 - 稿件作者校对.docx"),
        ("07", "Long_Chen", "7/返修版（无修订标记）-驳斥逻辑多元论—基于逻辑中隐式与显式立场.docx"),
        ("08", "LingXin_Cai", "8/密涅瓦猫头鹰问题的困境与自然规范性出路 20260114.docx"),
        ("09", "Zhien_Bei", "9/《命题的优先性》述评（最新修改版）.docx"),
        ("10", "Qiqi_Wang", "10/专访蒂莫西·威廉姆森教授（校正0916）.docx"),
    ):
        idx = str(int(no))
        manual_bib = "reference.bib"
        if no == "05":
            manual_bib = "WeiNong_Li.bib"
        elif no == "08":
            manual_bib = "LingXin_Cai.bib"
        cases.append(CaseSpec(key=f"86-{no}", issue="86", author=author,
                              author_dir=idx, manual_dir=f"{no} {author}",
                              source=source, manual_bib=manual_bib))
    return cases


# 已知缺口（Phase0 盘点 §4.2/§4.3 + 案例 141–144 登记）：code → 说明。
# 报告里给 finding 标「已知」；**不 suppress**（基线要看见真实状态，修完应自然消失）。
KNOWN: dict[str, dict[str, str]] = {
    "87-2": {
        "PAGE_DIFF": "MathType/OLE 公式丢失（源文件能力，案例 141 已登记）",
        "PLACEHOLDER": "源稿缺作者块 5 项，引擎如实报「未识别」",
    },
    "87-3": {
        "PAGE_DIFF": "`<w:sym>` LogicA 映射边界 + `.doc` 转 docx 公式拆碎（案例 141/142）",
        "CITE_DIFF": "引文内脚注里的文献引用未提升（案例 141 已登记，42 vs 47）",
        "BIB_COUNT_DIFF": "人工 bib 24 条比生成多 1 条（脚注文献，案例 142）",
    },
    "87-4": {
        "PLACEHOLDER": "通用 LaTeX 模板（非本刊 my* 宏）元数据 11 项缺（案例 141）",
        "BIB_FIELDS": "源 ref.bib 缺 address/booktitle 等，人工逐条补（案例 141 边界）",
    },
    "87-5": {
        "NO_CITES": "源稿编号引用 ↔ 无编号人年制清单，引擎不猜（P0-1 结论，案例 143）",
        "PAGE_DIFF": "参考文献区为空 + 公式内 CJK（案例 141/144）",
        "BIB_COUNT_DIFF": "人工 bib 25 条 vs 生成 22 条（案例 141；P0-2 修复后应变好）",
        "SECTION_DIFF": "节结构 6 vs 人工 7（案例 141）",
    },
    "87-6": {
        "CITE_DIFF": "正文引用只关联到 4 个条目（P0-1，案例 143）",
        "PAGE_DIFF": "人工 12 页 vs 生成 11（案例 141）",
        "BIB_COUNT_DIFF": "人工 23 条 vs 生成 22（案例 141）",
    },
    "87-7": {
        "NO_CITES": "源稿正文无引用命令（P0-1，案例 143）",
        "PAGE_DIFF": "人工 14 页 vs 生成 15（案例 141）",
        "BIB_COUNT_DIFF": "P0-5 修复后 16 vs 人工 14（案例 143 边界）",
    },
    "87-8": {
        "CITE_DIFF": "源稿编号与清单条数不一致，11 处保留原文（案例 141）",
        "PAGE_DIFF": "人工 17 页 vs 生成 16（案例 141）",
    },
    "87-9": {
        "PAGE_DIFF": "访谈体结构未识别（案例 141）",
        "SECTION_DIFF": "访谈体 0 section vs 人工 3（案例 141）",
    },
    "90-5": {},
}


def is_known(key: str, code: str) -> str | None:
    """返回该案例该 code 的已知说明；不认识返回 None。"""
    return KNOWN.get(key, {}).get(code)


# ─────────────────────────────────────────────────────────────────────────────
# 基础工具
# ─────────────────────────────────────────────────────────────────────────────


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_text(path: Path | str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def sha256_file(path: Path | str) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_pages(path: Path | str) -> int | None:
    p = Path(path)
    if not p.is_file() or PdfReader is None:
        return None
    try:
        return len(PdfReader(str(p)).pages)
    except Exception:
        return None


def sanitize_env(base: dict | None = None) -> tuple[dict, list[str]]:
    """清空付费相关环境变量，返回 (新 env, 被清掉的变量名)。纯函数，便于单测。"""
    env = dict(os.environ if base is None else base)
    cleared: list[str] = []
    for name in list(env):
        lowered = name.upper()
        if name in PAID_ENV_NAMES or any(lowered.startswith(p) for p in PAID_ENV_PREFIXES):
            cleared.append(name)
            del env[name]
    # 引擎子进程需要 vendor（lxml/pypdf）；不覆盖调用方已显式给的值
    pypath = env.get("PYTHONPATH", "")
    parts = [p for p in pypath.split(os.pathsep) if p]
    for want in (str(ENGINE), str(VENDOR)):
        if want not in parts:
            parts.insert(0, want)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env, sorted(cleared)


# 付费参数黑名单：批量脚本的子进程命令里**绝不允许**出现（安全网；构造命令后断言一次）
FORBIDDEN_ARG_PREFIXES = ("--verify-refs", "--llm-")


def assert_zero_cost_args(argv: list[str]) -> tuple[bool, list[str]]:
    """检查一条子进程命令是否触碰付费链路；返回 (是否安全, 命中项)。纯函数，便于单测。"""
    bad = [a for a in argv if a.startswith(FORBIDDEN_ARG_PREFIXES)]
    return (not bad), bad


def find_soffice() -> str | None:
    for cand in (
        "/opt/homebrew/bin/soffice",
        "/usr/local/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(cand).is_file():
            return cand
    return None


def run_cmd(cmd: list[str], *, cwd: Path, env: dict, log_path: Path,
            timeout: float) -> tuple[int, str, float]:
    """跑子进程，完整 stdout/stderr 落日志；返回 (rc, 日志文本, 耗时秒)。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("$ " + shlex.join(cmd) + "\n")
        f.write(f"# cwd: {cwd}\n# 开始: {now_iso()}\n\n")
        f.flush()
        try:
            proc = subprocess.run(
                cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, errors="replace",
                timeout=timeout,
            )
            out, rc = proc.stdout or "", proc.returncode
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            if isinstance(partial, bytes):  # pragma: no cover - text=True 时不会走到
                partial = partial.decode("utf-8", "replace")
            out, rc = str(partial) + f"\n[TIMEOUT {timeout:g}s]\n", 124
        except OSError as exc:
            out, rc = f"[OSError] {exc}\n", 127
        f.write(out)
        f.write(f"\n# 结束: {now_iso()}  耗时 {time.time() - t0:.1f}s  rc={rc}\n")
    return rc, read_text(log_path), time.time() - t0


# ─────────────────────────────────────────────────────────────────────────────
# 解析器：把 /tmp/slcn87/analyze.py + /tmp/verify87/run_all.py 的逻辑搬进来
#   （可复制关键正则/判据；运行时不依赖任何 /tmp 文件）
# ─────────────────────────────────────────────────────────────────────────────


def tex_stats(text: str) -> dict:
    """产物/人工 .tex 的结构计数（正文区 = \\begin{document} 之后）。"""
    body = text.split(r"\begin{document}", 1)[-1] if r"\begin{document}" in text else text
    return {
        "bytes": len(text.encode("utf-8")),
        "body_chars": len(re.sub(r"\s", "", body)),
        "section": len(re.findall(r"\\section\*?\{", body)),
        "subsection": len(re.findall(r"\\subsection\*?\{", body)),
        "tabular": len(re.findall(r"\\begin\{tabular\}", body)),
        "figure": len(re.findall(r"\\begin\{figure\}", body)),
        "includegraphics": len(re.findall(r"\\includegraphics", body)),
        "equation": len(re.findall(r"\\begin\{(?:equation|align|gather)\*?\}", body)),
        "parencite": len(re.findall(r"\\parencite", body)),
        "cite": len(re.findall(r"\\(?:parencite|cite|citep|citet|textcite|footcite)\b", body)),
        "placeholder_unrecognized": len(re.findall(r"【未识别", text)),
        "placeholder_any": len(re.findall(r"【[^】]*】", text)),
    }


def bib_stats(text: str) -> dict:
    """bib 条目/类型/必补字段/重复键（复用引擎 bibcheck/bibnorm）。"""
    entries = list(_split_bib_entries(text))
    keys = [k for _etype, k, _body in entries]
    rep = _bib_check(text)
    return {
        "entries": len(entries),
        "dup_keys": sorted({k for k in keys if keys.count(k) > 1}),
        "must_missing": rep.n_must,
        "nice_missing": rep.n_nice,
        "unstructured": rep.n_unstructured,
        "types": dict(rep.by_type),
    }


def log_stats(text: str) -> dict:
    """xelatex .log 判据：! 错误 / undefined / 缺字符 / 页数 / overfull。"""
    errors = re.findall(r"^! (.+)$", text, re.M)
    pages_m = re.search(r"Output written on .*?\((\d+) pages?", text)
    warn_cats: dict[str, int] = {}
    for mm in re.finditer(r"(?:LaTeX|Package|Class) (\w+) Warning: ([^\n]{0,80})", text):
        kind = mm.group(1) + ":" + mm.group(2).split(".")[0][:40]
        warn_cats[kind] = warn_cats.get(kind, 0) + 1
    return {
        "errors": errors,
        "n_errors": len(errors),
        "undefined_citation": len(re.findall(r"Citation '[^']*'[^\n]*undefined", text)),
        "undefined_reference": len(re.findall(r"Reference '[^']*'[^\n]*undefined", text)),
        "missing_char": len(re.findall(r"Missing character:", text)),
        "missing_char_samples": [f"U+{cp}" for cp in re.findall(
            r"Missing character: There is no .*?\(U\+([0-9A-Fa-f]{4,6})\)", text)][:6],
        "overfull": len(re.findall(r"Overfull \\[hv]box", text)),
        "underfull": len(re.findall(r"Underfull \\[hv]box", text)),
        "log_pages": int(pages_m.group(1)) if pages_m else None,
        "warning_cats": dict(sorted(warn_cats.items(), key=lambda kv: -kv[1])[:10]),
    }


def biber_stats(text: str) -> dict:
    errs = [ln for ln in text.splitlines() if ln.startswith("ERROR")]
    return {
        "errors": len(errs),
        "warnings": sum(1 for ln in text.splitlines() if ln.startswith("WARN")),
        "error_lines": errs[:4],
    }


def convert_log_stats(text: str) -> dict:
    """convert.py 的 console 判据：编译是否成功 / 警告 / 未识别计数。"""
    compile_ok = None
    sec = None
    if "⑨ 编译" in text:
        sec = text.split("⑨ 编译", 1)[1]
        compile_ok = "✅" in sec and "❌ 编译未通过" not in sec.split("⑩", 1)[0]
    pages_m = re.search(r"PDF 共 (\d+) 页", text)
    warnings = [ln.strip() for ln in text.splitlines()
                if ln.strip().startswith("·") or ln.strip().startswith("⚠")]
    auth_m = re.search(r"第一作者\s+(.+?)\s+←", text)
    title_m = re.search(r"题名《(.+?)》", text)
    return {
        "compile_ok": compile_ok,
        "log_pages": int(pages_m.group(1)) if pages_m else None,
        "n_warnings": len(warnings),
        "warnings": warnings[:20],
        "first_author": auth_m.group(1) if auth_m else None,
        "title": title_m.group(1) if title_m else None,
    }


def parse_plan_ai(text: str) -> tuple[bool, int | None]:
    """零付费断言：解析 `--verify-plan` 日志的「预计 AI：N 次」。

    返回 (是否确认 0 次, 解析到的次数)。配置了 LLM 时会打印「≤ N 次调用」，此时不满足
    零付费断言（N 可能为 0 也要求明确 0）；没有这一行同样视为无法确认（False）。
    """
    m = re.search(r"预计 AI[：:]\s*(?:≤\s*)?(\d+)\s*次", text)
    n = int(m.group(1)) if m else None
    return (n == 0), n


def parse_proofcheck_md(text: str) -> dict:
    rules = {m.group(1): int(m.group(2))
             for m in re.finditer(r"^\| `([A-Z]+-\d+)` \| \*\*(\d+)\*\*", text, re.M)}
    tot = re.search(r"合计命中 (\d+) 处", text)
    return {"rules": rules, "total": int(tot.group(1)) if tot else None}


def parse_proofcheck_console(text: str) -> dict:
    m = re.search(r"命中 (\d+) 处（可自动改 (\d+) 处）", text)
    if not m:
        return {"hits": None, "fixable": None}
    return {"hits": int(m.group(1)), "fixable": int(m.group(2))}


def parse_checkbib_md(text: str) -> dict:
    m = re.search(r"\*\*结论：(\d+) 处问题 / (\d+) 处提示\*\*", text)
    ent = re.search(r"解析出 (\d+) 条条目", text)
    return {
        "problems": int(m.group(1)) if m else None,
        "hints": int(m.group(2)) if m else None,
        "parse_entries": int(ent.group(1)) if ent else None,
    }


def citations_closure(tex_text: str, bib_text: str) -> dict:
    """正文引用 ↔ bib 闭合（复用 slcn.bibcheck 的键解析——唯一口径）。"""
    cited = _cited_keys(tex_text)
    keys = _entry_keys(bib_text)
    return {
        "n_cited": len(cited),
        "n_bib": len(keys),
        "dangling": sorted(cited - keys),
        "unused": sorted(keys - cited),
        "n_dangling": len(cited - keys),
        "n_unused": len(keys - cited),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 分级：纯函数（单测覆盖），不读文件
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    level: str   # P0 / P1 / P2
    code: str
    detail: str

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "detail": self.detail}


def _p0(m: dict, code: str, detail: str) -> Finding:
    return Finding("P0", code, detail)


def grade_case(m: dict, man: dict | None) -> list[Finding]:
    """把一例 metrics 判成 P0/P1/P2（man=None 时只做绝对判据）。"""
    out: list[Finding] = []

    # ── P0：绝对判据（不依赖人工成品）──────────────────────────────────
    if m.get("skipped"):
        return out
    rc = m.get("convert_rc")
    if rc != 0:
        out.append(_p0(m, "CONVERT_RC", f"convert rc={rc}（应为 0）"))
    if not m.get("pdf_pages"):
        out.append(_p0(m, "NO_PDF", "未生成 PDF 或 PDF 页数读不出"))
    log = m.get("log") or {}
    if log.get("n_errors"):
        out.append(_p0(m, "TEX_ERROR", f"编译 ! 错误 {log['n_errors']} 处：{log['errors'][:2]}"))
    if log.get("undefined_citation"):
        out.append(_p0(m, "UNDEF_CITE", f"undefined citation {log['undefined_citation']} 处"))
    if log.get("undefined_reference"):
        out.append(_p0(m, "UNDEF_REF", f"undefined reference {log['undefined_reference']} 处"))
    if log.get("missing_char"):
        out.append(_p0(m, "MISSING_CHAR", f"Missing character {log['missing_char']} 处"))
    if m.get("biber", {}).get("errors"):
        out.append(_p0(m, "BIBER_ERROR", f"biber ERROR {m['biber']['errors']} 条：{m['biber']['error_lines']}"))
    if m.get("sha_ok") is False:
        out.append(_p0(m, "NONDETERMINISTIC", "两遍 .tex/.bib sha256 不一致"))
    plan = m.get("plan") or {}
    if plan.get("ai_zero") is False:
        out.append(_p0(m, "ZERO_COST", f"零付费断言失败（预计 AI={plan.get('ai')}，rc={plan.get('rc')}）"))
    closure = m.get("closure") or {}
    if closure.get("n_dangling"):
        out.append(_p0(m, "DANGLING_CITE", f"悬空引用 {closure['n_dangling']} 条：{closure['dangling'][:5]}"))
    tex = m.get("tex") or {}
    bib = m.get("bib") or {}
    if tex.get("cite") == 0 and (bib.get("entries") or 0) > 0:
        out.append(_p0(m, "NO_CITES",
                       f"正文 0 条引用命令但 bib 有 {bib['entries']} 条 → PDF 参考文献区会是空的"))
    if m.get("doc_convert") and m["doc_convert"].get("needed") and not m["doc_convert"].get("ok"):
        out.append(_p0(m, "DOC_CONVERT", f".doc→docx 失败：{m['doc_convert'].get('error')}"))

    # ── 与人工成品的对照判据 ───────────────────────────────────────────
    if man:
        man_pages = man.get("pages")
        gen_pages = m.get("pdf_pages")
        if man_pages and gen_pages:
            diff = abs(gen_pages - man_pages)
            if diff >= PAGE_DIFF_P0:
                out.append(_p0(m, "PAGE_DIFF", f"页数 {gen_pages} vs 人工 {man_pages}（差 {diff}）"))
            elif diff == PAGE_DIFF_P1:
                out.append(Finding("P1", "PAGE_DIFF", f"页数 {gen_pages} vs 人工 {man_pages}（差 1）"))
        man_bib = man.get("bib_entries")
        gen_bib = (m.get("bib") or {}).get("entries")
        if man_bib and gen_bib is not None:
            diff = abs(gen_bib - man_bib)
            if diff >= BIB_DIFF_P0_MIN:
                out.append(_p0(m, "BIB_COUNT_DIFF", f"文献 {gen_bib} 条 vs 人工 {man_bib} 条（差 {diff}）"))
            elif diff:
                out.append(Finding("P1", "BIB_COUNT_DIFF", f"文献 {gen_bib} 条 vs 人工 {man_bib} 条（差 {diff}）"))
        man_cite = man.get("cite") or 0
        gen_cite = tex.get("cite") or 0
        if man_cite > 0 and gen_cite > 0:
            rel = abs(gen_cite - man_cite) / man_cite
            if rel > CITE_REL_DIFF:
                out.append(Finding("P1", "CITE_DIFF",
                                   f"正文引用 {gen_cite} vs 人工 {man_cite}（{rel:.0%}）"))
        man_ph = man.get("placeholders") or 0
        gen_ph = tex.get("placeholder_any") or 0
        if gen_ph > man_ph:
            out.append(Finding("P1", "PLACEHOLDER", f"占位符 {gen_ph} 处 vs 人工 {man_ph} 处"))
        man_sec = man.get("section")
        gen_sec = tex.get("section")
        if man_sec is not None and gen_sec is not None and man_sec != gen_sec:
            out.append(Finding("P1", "SECTION_DIFF", f"节标题 {gen_sec} vs 人工 {man_sec}"))
        gen_unused = closure.get("n_unused")
        man_unused = (man.get("closure") or {}).get("n_unused")
        if gen_unused is not None and man_unused is not None:
            excess = gen_unused - man_unused
            if excess > UNUSED_EXCESS_P1:
                out.append(Finding("P1", "UNUSED_BIB_EXCESS",
                                   f"未被引用条目比人工多 {excess} 条（生成 {gen_unused} / 人工 {man_unused}）"))
            elif excess > 0:
                out.append(Finding("P2", "UNUSED_BIB_MINOR",
                                   f"未被引用条目比人工多 {excess} 条（生成 {gen_unused} / 人工 {man_unused}）"))
        rep = m.get("checkbib") or {}
        gen_must = (m.get("bib") or {}).get("must_missing")
        man_must = man.get("bib_must")
        if gen_must is not None and man_must is not None and gen_must - man_must > BIB_MUST_EXCESS:
            out.append(Finding("P1", "BIB_FIELDS",
                               f"bib 必补字段 {gen_must} 项 vs 人工 {man_must} 项"))
        proof = m.get("proof") or {}
        if (proof.get("fixable") or 0) > 0:
            out.append(Finding("P1", "PROOF_FIXABLE",
                               f"一校可自动改残留 {proof['fixable']} 处（初排未接 fixer）"))
        gen_zh08 = (proof.get("rules") or {}).get("ZH-08", 0)
        man_zh08 = ((man.get("proof") or {}).get("rules") or {}).get("ZH-08", 0)
        if gen_zh08 > man_zh08:
            out.append(Finding("P2", "ZH08_HYPHEN",
                               f"中文旁半角短杠 ZH-08 {gen_zh08} 处 vs 人工 {man_zh08} 处"))
    return out


def case_level(findings: list[Finding]) -> str:
    if any(f.level == "P0" for f in findings):
        return "P0"
    if any(f.level == "P1" for f in findings):
        return "P1"
    if any(f.level == "P2" for f in findings):
        return "P2"
    return "✅"


# ─────────────────────────────────────────────────────────────────────────────
# 人工成品（稿件仓，只读）对照
# ─────────────────────────────────────────────────────────────────────────────


def manual_metrics(case: CaseSpec, man_root: Path, workdir: Path,
                   py: str, env: dict, logs_dir: Path) -> dict | None:
    """只读计算人工成品基准；目录/文件不全时返回 None（不报错）。"""
    d = man_root / case.manual_dir if case.manual_dir else None
    if not d or not d.is_dir():
        return None
    author = case.author
    tex_p, pdf_p, bib_p = d / f"{author}.tex", d / f"{author}.pdf", d / case.manual_bib
    out: dict = {"dir": str(d), "tex": str(tex_p), "pdf": str(pdf_p), "bib": str(bib_p)}
    out["exists"] = {"tex": tex_p.is_file(), "pdf": pdf_p.is_file(), "bib": bib_p.is_file()}
    out["pages"] = pdf_pages(pdf_p)
    tex_text = read_text(tex_p)
    bib_text = read_text(bib_p) if bib_p.is_file() else ""
    ts = tex_stats(tex_text)
    out["section"] = ts["section"]
    out["cite"] = ts["cite"]
    out["placeholders"] = ts["placeholder_any"]
    out["bib_entries"] = None
    out["bib_must"] = None
    if bib_p.is_file():
        bs = bib_stats(bib_text)
        out["bib_entries"] = bs["entries"]
        out["bib_must"] = bs["must_missing"]
        out["bib_types"] = bs["types"]
    out["closure"] = citations_closure(tex_text, bib_text) if tex_p.is_file() and bib_p.is_file() else {}
    # 人工成品一校基线（证明「生成多出来的命中」是真的差异，而不是判据差异）
    proof: dict = {}
    if tex_p.is_file():
        md = logs_dir / "proof" / f"{case.key}_man.md"
        log = logs_dir / "proof" / f"{case.key}_man.log"
        rc, text, _ = run_cmd([py, "proofcheck.py", "--tex", str(tex_p), "--out", str(md)],
                              cwd=ENGINE, env=env, log_path=log, timeout=120)
        proof = {**parse_proofcheck_console(text), **parse_proofcheck_md(read_text(md)), "rc": rc}
    out["proof"] = proof
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 全量基准（task-40）：repo 扫描 / 失败聚类 / dashboard / CSV
# ─────────────────────────────────────────────────────────────────────────────

# 根因聚类元数据：code → (中文标签, 建议承接角色, 预估工作量 S/M/L)
CLUSTER_META: dict[str, tuple[str, str, str]] = {
    "CONVERT_RC": ("转换失败（rc≠0）", "engine-core", "M"),
    "TEX_ERROR": ("LaTeX 编译错误", "engine-core", "M"),
    "UNDEF_CITE": ("未定义引用（citation）", "bib-refs", "S"),
    "UNDEF_REF": ("未定义交叉引用（reference）", "engine-core", "S"),
    "MISSING_CHAR": ("缺字符（字体/数学模式）", "engine-core", "S"),
    "BIBER_ERROR": ("biber 错误", "bib-refs", "S"),
    "NONDETERMINISTIC": ("两遍 sha256 不一致", "engine-core", "M"),
    "ZERO_COST": ("零付费断言失败", "case-lab", "S"),
    "DANGLING_CITE": ("悬空引用（正文键不在 bib）", "bib-refs", "S"),
    "NO_CITES": ("正文 0 引用（参考文献区空）", "bib-refs", "M"),
    "DOC_CONVERT": (".doc→docx 失败", "engine-core", "S"),
    "PAGE_DIFF": ("页数偏差", "engine-core", "M"),
    "BIB_COUNT_DIFF": ("文献条数偏差", "bib-refs", "M"),
    "CITE_DIFF": ("正文引用数偏差", "bib-refs", "M"),
    "SECTION_DIFF": ("节结构偏差", "engine-core", "M"),
    "PLACEHOLDER": ("元数据占位符（未识别）", "engine-core", "M"),
    "PROOF_FIXABLE": ("一校可自动改残留", "engine-core", "S"),
    "BIB_FIELDS": ("bib 必补字段过多", "bib-refs", "L"),
    "UNUSED_BIB_EXCESS": ("未引用条目过多", "bib-refs", "M"),
    "UNUSED_BIB_MINOR": ("未引用条目小幅多出", "bib-refs", "S"),
    "ZH08_HYPHEN": ("中文旁半角短杠（口径待迁移）", "rules-docs", "S"),
}
LEVEL_WEIGHT = {"P0": 100, "P1": 10, "P2": 1}


def _parse_issue_code(issue_dir_name: str) -> str:
    """从期目录名 `20xx年第N期(87)` 里取 issue code（`87` / `67A`）。"""
    m = re.search(r"\((\d+[A-Za-z]?)\)\s*$", issue_dir_name)
    return m.group(1) if m else ""


def scan_repo_cases(repo_root: Path,
                    skip_codes: set[str] | None = None) -> tuple[list[CaseSpec], dict]:
    """只读扫描稿件仓，返回「有源稿 + 有人工成品 tex」的期篇 CaseSpec（+ 统计）。

    判据（全部只读，不写稿件仓）：
    - 期目录名 `20xx年第N期(NN)`；**跳过 Desktop 工作区已覆盖的 issue**（默认 87/88/89/90）；
    - 篇目录名 `NN 作者`，必须存在 `<作者>.tex` 人工成品；
    - 源稿用**引擎 `sources.identify()`** 识别（与 corpus/回归器同一套逻辑，不另写判据）；
      若识别出的正文源 == 人工成品 tex → 视为「无源稿」跳过（不能拿人工成品当输入）；
    - 人工 bib：`reference.bib` 优先，否则 `<作者>.bib`。
    """
    skip = set(REPO_SKIP_ISSUE_CODES if skip_codes is None else skip_codes)
    cases: list[CaseSpec] = []
    stats = {"issues": 0, "articles": 0, "cases": 0,
             "skipped_no_manual_tex": 0, "skipped_no_source": 0,
             "skipped_manual_is_source": 0, "by_channel": {"word": 0, "latex": 0}}
    for issue_dir in sorted(repo_root.glob("20*")):
        if not issue_dir.is_dir():
            continue
        if _parse_issue_code(issue_dir.name) in skip:
            continue
        stats["issues"] += 1
        for art in sorted(issue_dir.iterdir()):
            if not art.is_dir() or art.name.startswith("000"):
                continue
            m = re.match(r"^(\d{2})\s+(.+)$", art.name)
            if not m:
                continue
            stats["articles"] += 1
            no, author = m.group(1), m.group(2).strip()
            manual_tex = art / f"{author}.tex"
            if not manual_tex.is_file():
                stats["skipped_no_manual_tex"] += 1
                continue
            try:
                ss = _sources.identify(str(art))
            except Exception:  # noqa: BLE001 - 识别失败按「无源稿」跳过
                ss = None
            # 源稿候选：Word 优先（identify 的 ss.docx = 挑出的正文 docx），否则 tex 正文源
            word_src = getattr(ss, "docx", None) if ss is not None else None
            body = ss.body.path if (ss is not None and ss.body is not None) else None
            cand = None
            if word_src:
                cand = Path(word_src)
            elif body:
                cand = Path(body)
            if cand is None:
                stats["skipped_no_source"] += 1
                continue
            body_p = cand.resolve()
            if body_p == manual_tex.resolve():
                stats["skipped_manual_is_source"] += 1
                continue
            ch = "word" if body_p.suffix.lower() in (".docx", ".doc") else "latex"
            mb = art / "reference.bib"
            if not mb.is_file():
                alt = art / f"{author}.bib"
                if alt.is_file():
                    mb = alt
                else:  # 兜底：目录内最大 .bib（如 rongliwu.bib / dissertation5.bib）
                    bibs = sorted(art.glob("*.bib"), key=lambda p: p.stat().st_size, reverse=True)
                    if bibs:
                        mb = bibs[0]
            cases.append(CaseSpec(
                key=f"repo-{_parse_issue_code(issue_dir.name)}-{no}", issue="repo",
                author=author, author_dir=".",
                manual_dir=str(art.relative_to(repo_root)), manual_bib=mb.name,
                source=str(body_p), workspace=str(art.resolve()),
                manual_root=str(repo_root.resolve())))
            stats["cases"] += 1
            stats["by_channel"][ch] += 1
    return cases, stats


def error_profile(m: dict) -> dict:
    """失败/缺字的第一现场签名（dashboard 根因聚类用）。"""
    log = m.get("log") or {}
    rc = m.get("convert_rc")
    out = {"code": "", "signature": ""}
    errors = log.get("errors") or []
    if rc not in (0, None):
        out["code"] = "E_CONVERT_RC"
        out["signature"] = (errors[0] if errors else f"rc={rc}")[:100]
    elif log.get("n_errors"):
        out["code"] = "E_TEX"
        out["signature"] = (errors[0] if errors else "")[:100]
    elif (m.get("biber") or {}).get("errors"):
        out["code"] = "E_BIBER"
        out["signature"] = str(((m.get("biber") or {}).get("error_lines") or [""])[0])[:100]
    elif log.get("missing_char"):
        out["code"] = "E_MISSING_CHAR"
        out["signature"] = ",".join((log.get("missing_char_samples") or [])[:4])
    elif rc == 0 and "pdf_pages" in m and not m.get("pdf_pages") and not m.get("skipped"):
        out["code"] = "E_NO_PDF"
    return out


def build_dashboard(run: dict) -> list[dict]:
    """把 findings 按 (P0/P1/P2 × 根因 code) 聚合，按「严重度加权 × 影响篇数」排序。"""
    recs: dict[tuple[str, str], dict] = {}
    for key in run.get("case_order", []):
        m = run["cases"].get(key) or {}
        if m.get("skipped"):
            continue
        ep = error_profile(m)
        for f in m.get("findings") or []:
            code = f["code"]
            ck = (f["level"], code)
            rec = recs.setdefault(ck, {"level": f["level"], "code": code,
                                       "cases": [], "count": 0,
                                       "signatures": [], "examples": []})
            rec["cases"].append(key)
            rec["count"] += 1
            sig = ep.get("signature") if code in ("CONVERT_RC", "TEX_ERROR", "MISSING_CHAR") else ""
            if sig and sig not in rec["signatures"]:
                rec["signatures"].append(sig)
            if len(rec["examples"]) < 3:
                rec["examples"].append(f"{key}: {str(f.get('detail', ''))[:120]}")
    rows: list[dict] = []
    for (_level, code), rec in recs.items():
        label, owner, effort = CLUSTER_META.get(code, (code, "待定", "?"))
        case_keys = sorted(set(rec["cases"]))
        rows.append({
            "rank": 0, "level": rec["level"], "cluster_code": code, "cluster": label,
            "owner": owner, "effort": effort,
            "cases_count": len(case_keys), "findings_count": rec["count"],
            "case_keys": ";".join(case_keys[:12]) + (";…" if len(case_keys) > 12 else ""),
            "signatures": " | ".join(rec["signatures"][:3]),
            "examples": " | ".join(rec["examples"]),
            "priority": LEVEL_WEIGHT.get(rec["level"], 1) * len(case_keys),
        })
    rows.sort(key=lambda r: (-r["priority"], r["level"], r["cluster_code"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def write_dashboard_csv(path: Path, rows: list[dict]) -> None:
    fields = ["rank", "level", "cluster_code", "cluster", "owner", "effort",
              "cases_count", "findings_count", "priority", "case_keys",
              "signatures", "examples"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def write_cases_csv(path: Path, run: dict) -> None:
    fields = ["key", "issue", "author", "channel", "level", "convert_rc",
              "pdf_pages", "manual_pages", "bib_entries", "manual_bib_entries",
              "cite", "manual_cite", "section", "manual_section",
              "equation", "tabular", "figure", "missing_char", "n_errors",
              "undef_cite", "undef_ref", "dangling", "unused", "placeholder",
              "sim_mode", "sim_raw", "sim_content", "l2", "l3", "elapsed_s",
              "error_code", "error_signature", "findings"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for key in run.get("case_order", []):
            m = run["cases"].get(key) or {}
            if m.get("skipped"):
                w.writerow({"key": key, "issue": m.get("issue", ""), "author": m.get("author", ""),
                            "level": "SKIP", "error_code": m.get("skipped", "")})
                continue
            man = m.get("manual") or {}
            tex = m.get("tex") or {}
            log = m.get("log") or {}
            bib = m.get("bib") or {}
            clo = m.get("closure") or {}
            sim = m.get("similarity") or {}
            ep = m.get("error_profile") or {}
            w.writerow({
                "key": key, "issue": m.get("issue", ""), "author": m.get("author", ""),
                "channel": m.get("channel", ""), "level": m.get("level", ""),
                "convert_rc": m.get("convert_rc"), "pdf_pages": m.get("pdf_pages"),
                "manual_pages": man.get("pages"), "bib_entries": bib.get("entries"),
                "manual_bib_entries": man.get("bib_entries"), "cite": tex.get("cite"),
                "manual_cite": man.get("cite"), "section": tex.get("section"),
                "manual_section": man.get("section"), "equation": tex.get("equation"),
                "tabular": tex.get("tabular"), "figure": tex.get("figure"),
                "missing_char": log.get("missing_char"), "n_errors": log.get("n_errors"),
                "undef_cite": log.get("undefined_citation"),
                "undef_ref": log.get("undefined_reference"),
                "dangling": clo.get("n_dangling"), "unused": clo.get("n_unused"),
                "placeholder": tex.get("placeholder_any"),
                "sim_mode": sim.get("mode", ""),
                "sim_raw": sim.get("raw"), "sim_content": sim.get("content"),
                "l2": sim.get("l2"), "l3": sim.get("l3"),
                "elapsed_s": m.get("elapsed_s"),
                "error_code": ep.get("code", ""), "error_signature": ep.get("signature", ""),
                "findings": ";".join(f"{x['level']}:{x['code']}" for x in m.get("findings") or []),
            })


def estimate_bench(cases: list[CaseSpec]) -> dict:
    """重活窗口申请用：篇数、按 issue/通道、预计耗时（秒/分钟）。"""
    by_issue: dict[str, int] = {}
    by_channel = {"word": 0, "latex": 0, "other": 0}
    for c in cases:
        by_issue[c.issue] = by_issue.get(c.issue, 0) + 1
        sfx = Path(c.source).suffix.lower() if c.source else ""
        if sfx in (".docx", ".doc"):
            by_channel["word"] += 1
        elif sfx == ".tex":
            by_channel["latex"] += 1
        else:
            by_channel["other"] += 1
    n = len(cases)
    sec = n * BENCH_EST_SEC_PER_CASE
    return {"cases": n, "by_issue": by_issue, "by_channel": by_channel,
            "est_seconds": round(sec, 1), "est_minutes": round(sec / 60, 1),
            "assumption_sec_per_case": BENCH_EST_SEC_PER_CASE}


# ─────────────────────────────────────────────────────────────────────────────
# 主流程：跑一个案例
# ─────────────────────────────────────────────────────────────────────────────


def discover_source(case: CaseSpec, workspace: Path) -> tuple[Path | None, str]:
    """在工作区 <author_dir>/ 里找主文件（可用 manifest 的 source 覆盖）。"""
    if case.source:
        raw = Path(case.source)
        p = raw if raw.is_absolute() else (workspace / case.source)
        return (p, f"manifest: {case.source}") if p.is_file() else (None, f"manifest 指定的源不存在：{p}")
    d = workspace / case.author_dir
    if not d.is_dir():
        return None, f"作者目录不存在：{d}"
    tex = sorted(d.glob("*.tex"))
    if tex:
        mains = [p for p in tex if p.name.lower() == "main.tex"]
        pick = mains[0] if mains else max(tex, key=lambda p: p.stat().st_size)
        return pick, f"自动发现（LaTeX 主文件）：{pick.name}"
    exclude = ("作者信息", "英文摘要", "摘要", "abstract", "版权", "协议", "说明", "cover", "template")
    docs = [p for p in (sorted(d.glob("*.docx")) + sorted(d.glob("*.doc")))
            if not any(w.lower() in p.name.lower() for w in exclude)]
    if not docs:
        return None, f"作者目录里没有可用 .docx/.doc/.tex：{d}"
    pick = max(docs, key=lambda p: p.stat().st_size)
    return pick, f"自动发现（Word 主文件）：{pick.name}"


def resolve_metadata_source(case: CaseSpec, workspace: Path) -> tuple[Path | None, dict]:
    """解析 manifest 的 `metadata`（附加源/元数据源），返回 (路径|None, info dict)。

    判据（与模块 docstring 一致）：
    - 空字符串 → 不传 `--metadata`（单源），info["declared"]=False；
    - 绝对路径 → 直接用；
    - 相对路径 → 依次尝试「工作区根 → 作者目录 → 源文件所在目录」；
    - **文件不存在 → 安全回退**：返回 None 且 info["fallback"]=True、info["note"] 写明尝试过的
      候选路径（案例照常跑、不阻断；报告附录会显示该 note，不静默）。
    """
    info: dict = {"declared": bool(case.metadata), "found": False, "fallback": False,
                  "path": None, "note": ""}
    if not case.metadata:
        info["note"] = "未声明附加源（单源）"
        return None, info
    raw = Path(case.metadata)
    if raw.is_absolute():
        candidates = [raw]
    else:
        candidates = [workspace / raw, workspace / case.author_dir / raw]
        if case.source:
            candidates.append((workspace / case.source).parent / raw)
    seen: set[str] = set()
    uniq: list[Path] = []
    for c in candidates:
        key = str(c)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    for c in uniq:
        if c.is_file():
            info.update(found=True, path=str(c), note=f"附加源 --metadata：{c}")
            return c, info
    info["fallback"] = True
    info["note"] = ("⚠ manifest 声明了附加源但文件不存在（尝试过："
                    + "、".join(str(c) for c in uniq) + "）→ 安全回退为单源")
    return None, info


def sanitize_output_name(author: str) -> str:
    """convert.py `--name` 只允许字母/数字/下划线且以字母开头；repo 作者名可能带空格或中文。

    只影响**产物目录名**；人工成品核对仍用原始作者名（`<Author>.tex` 不动）。
    """
    s = re.sub(r"[^A-Za-z0-9_]+", "_", author or "").strip("_")
    s = re.sub(r"_+", "_", s) or "Case"
    if not s[0].isalpha():
        s = "A_" + s
    return s


def sha_verdict(convert_rc: int | None, tex_exists: bool, bib_exists: bool,
                before: dict, after: dict) -> bool | None:
    """两遍确定性判定：只在转换成功且产物存在时判 True/False，否则 None（失败案例无法判）。"""
    if convert_rc == 0 and tex_exists and bib_exists:
        return before == after
    return None


def plan_ai_status(plan_rc: int | None, ai_n: int | None) -> bool | None:
    """零付费断言：True=确认「预计 AI：0 次」；False=确认非 0；None=未验证（plan 未成功）。"""
    if plan_rc != 0 or ai_n is None:
        return None
    return ai_n == 0


def similarity_choice(man_text: str, gen_text: str, requested: str = "auto") -> str:
    """相似度口径：none/full/auto——auto 时 ref+gen > SIM_FULL_MAX_CHARS 降级 fast。"""
    if requested in ("none", "full"):
        return requested
    total = len(man_text or "") + len(gen_text or "")
    return "full" if total <= SIM_FULL_MAX_CHARS else "fast"


def _fast_similarity(man_text: str, gen_text: str) -> dict:
    """O10 快速指标：行级 difflib + 前缀 quick_ratio（O(n)，不再做全文 token 级比对）。

    精度与 `slcn.score` 的全文指标不同（content 为上界近似），报告/cases.csv 用 `mode` 标注；
    只用于「大差异大稿」的排序参考，不影响 P0/P1/P2 判定（分级不读相似度）。
    """
    ref_n = _score.normalize_ws(man_text or "")
    gen_n = _score.normalize_ws(gen_text or "")
    raw = difflib.SequenceMatcher(None, ref_n.split("\n"), gen_n.split("\n"),
                                  autojunk=False).ratio()
    ref_c = ref_n.replace("\n", "")
    gen_c = gen_n.replace("\n", "")
    content = difflib.SequenceMatcher(
        None, ref_c[:SIM_FAST_CAP], gen_c[:SIM_FAST_CAP], autojunk=False).quick_ratio()
    return {"mode": "fast", "raw": round(raw, 4), "content": round(content, 4),
            "l2": None, "l3": None, "ref_chars": len(ref_c)}


def compute_similarity(man_text: str, gen_text: str, requested: str = "auto") -> dict:
    """按口径算相似度：none=跳过；full=引擎 slcn.score；auto=小稿 full、大稿 fast。"""
    mode = similarity_choice(man_text, gen_text, requested)
    if mode == "none":
        return {"mode": "skipped"}
    if mode == "full":
        sc = _score.score(man_text, gen_text)
        return {"mode": "full", "raw": round(float(sc.raw_ratio), 4),
                "content": round(float(sc.char_accuracy), 4),
                "l2": int(getattr(sc, "l2_blocks", 0) or 0),
                "l3": int(getattr(sc, "l3_blocks", 0) or 0),
                "ref_chars": int(getattr(sc, "ref_chars", 0) or 0)}
    return _fast_similarity(man_text, gen_text)


def tier_issue_arg(tier: str) -> str:
    """O12 分层：中间验收固定 5 期（4.2 min）；全量档=同 5 期 + --repo-scan（最终只跑一次）。"""
    return "86,87,88,89,90"


def tier_repo_scan(tier: str, explicit_repo_scan: bool = False) -> bool:
    """full 档强制 repo 扫描；mid 档尊重显式 --repo-scan（兼容旧命令）。"""
    return bool(explicit_repo_scan) or tier == "full"


def similarity_cache_key(man_text: str, gen_text: str, requested: str) -> str:
    """缓存键 = sha256(人工 tex) + sha256(生成 tex) + 口径（内容变则失效，无需额外版本号）。"""
    h = hashlib.sha256()
    h.update((man_text or "").encode("utf-8", "replace")); h.update(b"\0")
    h.update((gen_text or "").encode("utf-8", "replace")); h.update(b"\0")
    h.update((requested or "auto").encode("utf-8"))
    return h.hexdigest()[:32]


def similarity_cached(man_text: str, gen_text: str, requested: str,
                      cache: dict | None = None) -> tuple[dict, bool]:
    """按 (人工 sha, 生成 sha, 口径) 取相似度；命中返回 (result, True)，否则计算并写回 cache。"""
    if cache is None:
        return compute_similarity(man_text, gen_text, requested), False
    key = similarity_cache_key(man_text, gen_text, requested)
    if key in cache:
        return dict(cache[key]), True
    sim = compute_similarity(man_text, gen_text, requested)
    cache[key] = sim
    return sim, False


def convert_common_args(case: CaseSpec, src: Path, out_root: Path,
                        workspace: Path, use_metadata: bool = True) -> tuple[list[str], dict]:
    """拼 `convert.py` 的公共参数（source/out/name/附加源/extra_args），纯函数便于单测。

    `metadata` 只在文件解析成功时追加；`extra_args` 里已显式带 `--metadata` 时不重复追加
    （避免同一选项两次、语义含糊），此时 info["note"] 标明「由 extra_args 提供」。

    `use_metadata=False`（CLI `--no-metadata`）：**忽略** manifest 的 metadata 字段，并从
    `extra_args` 里剔除 `--metadata`/`--metadata=…`（含其后一个值参数）——用于「A2-only」
    对照口径：模拟不传附加源、只靠引擎通用发现（engine-core 另卡）的行为。
    """
    args = ["--source", str(src), "--out", str(out_root), "--name",
            sanitize_output_name(case.author)]
    extra = list(case.extra_args)
    if not use_metadata:
        cleaned: list[str] = []
        skip_next = False
        for a in extra:
            if skip_next:
                skip_next = False
                continue
            if a == "--metadata":
                skip_next = True
                continue
            if a.startswith("--metadata="):
                continue
            cleaned.append(a)
        if len(cleaned) != len(extra):
            extra = cleaned
        args += extra
        meta_info = {"declared": bool(case.metadata), "found": False, "fallback": False,
                     "path": None, "disabled": True,
                     "note": "已按 --no-metadata 禁用附加源（A2-only 口径：不传 --metadata）"}
        return args, meta_info
    args += extra
    has_meta_arg = any(a == "--metadata" or a.startswith("--metadata=") for a in extra)
    meta_path, meta_info = resolve_metadata_source(case, workspace)
    if meta_path is not None and not has_meta_arg:
        args += ["--metadata", str(meta_path)]
    elif has_meta_arg:
        meta_info = {**meta_info, "note": "附加源由 extra_args 提供（未重复追加）"}
    return args, meta_info


def ensure_docx(src: Path, workdir: Path, logs_dir: Path, env: dict) -> tuple[Path | None, dict]:
    """`.doc` → `/tmp` 副本 `.docx`（LibreOffice；绝不改 Desktop 源文件）。"""
    info: dict = {"needed": True, "ok": False, "source": str(src), "error": ""}
    if src.suffix.lower() != ".doc":
        info["needed"] = False
        info["ok"] = True
        return src, info
    soffice = find_soffice()
    if not soffice:
        info["error"] = "找不到 LibreOffice（soffice），无法转换 .doc"
        return None, info
    outdir = workdir / "doc2docx"
    outdir.mkdir(parents=True, exist_ok=True)
    profile = workdir / "lo_profile"
    log = logs_dir / "convert" / f"doc2docx_{src.stem}.log"
    cmd = [soffice, "--headless", f"-env:UserInstallation=file://{profile}",
           "--convert-to", "docx", "--outdir", str(outdir), str(src)]
    rc, text, _ = run_cmd(cmd, cwd=workdir, env=env, log_path=log, timeout=240)
    dst = outdir / (src.stem + ".docx")
    if not dst.is_file():  # 兜底：LibreOffice 偶尔改名
        cand = sorted(outdir.glob("*.docx"))
        dst = cand[0] if cand else dst
    info.update({"ok": rc == 0 and dst.is_file(), "path": str(dst), "rc": rc,
                 "error": "" if dst.is_file() else f"soffice rc={rc}，未产出 docx"})
    return (dst if dst.is_file() else None), info


def run_case(case: CaseSpec, workspace: Path, man_root: Path, out_root: Path,
             py: str, env: dict, timeout: float, compare: bool,
             use_metadata: bool = True, similarity: str = "auto",
             sim_cache: dict | None = None) -> dict:
    """跑一个案例：pass1 转换+编译 → 两遍 sha → 判据采集 → 人工对照。"""
    author, key = case.author, case.key
    out_name = sanitize_output_name(author)   # 产物目录名（repo 作者名可能含空格/中文）
    t0 = time.time()
    m: dict = {
        "key": key, "issue": case.issue, "author": author,
        "workspace": str(workspace), "started": now_iso(),
    }
    logs_dir = out_root / "logs"
    products = out_root / "products"

    # 1) 源文件（.doc 先转 /tmp 副本）
    src, src_note = discover_source(case, workspace)
    m["source_note"] = src_note
    if src is None:
        m.update({"skipped": src_note, "convert_rc": None, "pdf_pages": None,
                  "findings": [], "level": "SKIP", "elapsed_s": 0.0})
        return m
    m["source"] = str(src)
    workdir = out_root / "work" / key
    workdir.mkdir(parents=True, exist_ok=True)
    src, docinfo = ensure_docx(src, workdir, logs_dir, env)
    m["doc_convert"] = docinfo
    if src is None:
        m.update({"skipped": docinfo.get("error"), "convert_rc": None, "pdf_pages": None,
                  "findings": [], "level": "SKIP", "elapsed_s": time.time() - t0})
        return m

    common, meta_info = convert_common_args(case, src, products, workspace,
                                            use_metadata=use_metadata)
    m["metadata"] = meta_info

    # 零付费安全网：任何命令都不得触碰 --verify-refs / --llm-*（构造后断言）
    cmd1 = [py, "convert.py", *common, "--compile"]
    cmd2 = [py, "convert.py", *common, "--verify-plan", "--search-refs"]
    safe, bad = assert_zero_cost_args(cmd1 + cmd2)
    if not safe:
        raise RuntimeError(f"零付费被破坏：命令里出现付费参数 {bad}")

    # 2) pass1：转换 + 编译（判 rc / PDF / ! 错误 / 缺字符）
    pass1_log = logs_dir / "convert" / f"{key}_pass1.log"
    rc1, txt1, t1 = run_cmd(cmd1,
                            cwd=ENGINE, env=env, log_path=pass1_log, timeout=timeout)
    m["convert_rc"] = rc1
    m["elapsed_convert_s"] = round(t1, 1)
    cl = convert_log_stats(txt1)
    m["convert"] = cl

    prod = products / out_name
    tex_p, bib_p, pdf_p, log_p = (prod / f"{out_name}.tex", prod / "reference.bib",
                                  prod / f"{out_name}.pdf", prod / f"{out_name}.log")
    m["product_dir"] = str(prod)
    m["pdf_pages"] = pdf_pages(pdf_p)
    m["tex"] = tex_stats(read_text(tex_p)) if tex_p.is_file() else {}
    gen_bib_text = read_text(bib_p)
    m["bib"] = bib_stats(gen_bib_text) if bib_p.is_file() else {}
    m["log"] = log_stats(read_text(log_p)) if log_p.is_file() else {}
    m["biber"] = biber_stats(read_text(prod / f"{out_name}.blg")) if (prod / f"{out_name}.blg").is_file() else {}

    # 3) 两遍确定性：pass2 = --verify-plan（产物照常写出，重跑同目录）
    sha_before = {"tex": sha256_file(tex_p), "bib": sha256_file(bib_p)}
    pass2_log = logs_dir / "convert" / f"{key}_pass2_plan.log"
    rc2, txt2, t2 = run_cmd(cmd2,
                            cwd=ENGINE, env=env, log_path=pass2_log, timeout=timeout)
    sha_after = {"tex": sha256_file(tex_p), "bib": sha256_file(bib_p)}
    ai_zero, ai_n = parse_plan_ai(txt2)
    m["plan"] = {
        "rc": rc2, "elapsed_s": round(t2, 1), "ai": ai_n,
        "ai_zero": plan_ai_status(rc2, ai_n),
        "search_line": next((ln.strip() for ln in txt2.splitlines() if "预计检索" in ln), ""),
    }
    m["sha_before"], m["sha_after"] = sha_before, sha_after
    m["sha_ok"] = sha_verdict(rc1, tex_p.is_file(), bib_p.is_file(), sha_before, sha_after)

    # 4) 引用闭合（复用引擎键解析）
    gen_tex_text = read_text(tex_p)
    m["closure"] = citations_closure(gen_tex_text, gen_bib_text) if tex_p.is_file() else {}

    # 5) 一校核查（生成稿）
    if tex_p.is_file():
        md = logs_dir / "proof" / f"{key}_gen.md"
        log = logs_dir / "proof" / f"{key}_gen.log"
        rc, text, _ = run_cmd([py, "proofcheck.py", "--tex", str(tex_p), "--out", str(md)],
                              cwd=ENGINE, env=env, log_path=log, timeout=120)
        m["proof"] = {**parse_proofcheck_console(text), **parse_proofcheck_md(read_text(md)), "rc": rc}

    # 6) 人工成品对照（只读稿件仓）
    man = manual_metrics(case, man_root, workdir, py, env, logs_dir) if compare else None
    m["manual"] = man

    # 6b) 相似度（O10：auto 大稿降级 fast；O12：按 (人工 sha, 生成 sha, 口径) 缓存）
    if man and man.get("exists", {}).get("tex") and tex_p.is_file():
        try:
            man_text = read_text(man["tex"])
            sim, hit = similarity_cached(man_text, gen_tex_text, similarity, sim_cache)
            m["similarity"] = sim
            m["sim_cache"] = "hit" if hit else "miss"
        except Exception as exc:  # noqa: BLE001
            m["similarity"] = {"mode": "error", "error": str(exc)[:120]}
            m["sim_cache"] = "error"

    # 7) 文献回读校验（生成 bib vs 人工 bib；键方案不同，闭合以直接计算为准）
    if man and man.get("exists", {}).get("bib") and bib_p.is_file():
        md = logs_dir / "bib" / f"{key}.md"
        log = logs_dir / "bib" / f"{key}.log"
        rc, text, _ = run_cmd(
            [py, "checkbib.py", "--returned", str(bib_p),
             "--original", str(man["bib"]), "--tex", str(tex_p), "--out", str(md)],
            cwd=ENGINE, env=env, log_path=log, timeout=120)
        m["checkbib"] = {**parse_checkbib_md(read_text(md)), "rc": rc}

    # 8) 分级
    findings = grade_case(m, man)
    m["findings"] = [f.to_dict() for f in findings]
    m["level"] = case_level(findings)
    m["error_profile"] = error_profile(m)
    m["elapsed_s"] = round(time.time() - t0, 1)
    m["finished"] = now_iso()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# 报告
# ─────────────────────────────────────────────────────────────────────────────


def _fmt(v, empty: str = "—") -> str:
    if v is None:
        return empty
    return str(v)


def _diff_str(gen, man) -> str:
    g, m = _fmt(gen), _fmt(man)
    return f"{g}/{m}"


def render_report(run: dict) -> str:
    """把 run dict 渲染成 Markdown 主报告。"""
    cases: dict = run.get("cases", {})
    ordered = [cases[k] for k in run.get("case_order", []) if k in cases]
    totals = run.get("totals", {})
    lines: list[str] = []
    lines.append(f"# 批量实例测试报告：{run.get('run_id', '')}")
    lines.append("")
    lines.append(f"> 生成：{run.get('finished') or now_iso()}　｜　引擎 **{run.get('engine_version')}**　｜　"
                 f"git HEAD `{run.get('git_head')}`　｜　python `{run.get('python')}`")
    lines.append(f"> 产物/日志：`{run.get('out_dir')}`（**只写 /tmp**；未刷新 Desktop `testResult`，未 commit/push）")
    rs, re_ = run.get("repo_start") or {}, run.get("repo_end") or {}
    if rs and re_ and rs != re_:
        lines.append(">")
        lines.append(f"> ⚠ **基线有效性告警**：本次运行期间公开仓工作区发生变化"
                     f"（start HEAD `{rs.get('head')}` / {rs.get('status_lines')} 个脏文件 → "
                     f"end HEAD `{re_.get('head')}` / {re_.get('status_lines')} 个脏文件）。"
                     "**转换类判据不受影响**（`convert.py` 未被改），但一校核查等"
                     "「跑之前才读盘」的判据可能跨了两个版本，对比时留意。")
    lines.append("")
    lines.append("## 0. 结论")
    lines.append("")
    lines.append(f"- 跑完 **{totals.get('ran', 0)}/{totals.get('total', 0)}** 个案例"
                 f"（SKIP {totals.get('skipped', 0)} 个）。")
    lines.append(f"- **P0 {totals.get('p0', 0)} 项 / P1 {totals.get('p1', 0)} 项 / P2 {totals.get('p2', 0)} 项**；"
                 f"其中 P0 标「已知」{totals.get('p0_known', 0)} 项、**新增 P0 {totals.get('p0_new', 0)} 项**。")
    lines.append(f"- 零付费：{'✅ 全部案例「预计 AI：0 次」' if totals.get('zero_cost_ok') else '❌ 有案例不满足（见 P0 明细）'}；"
                 f"清空付费环境变量 {len(run.get('zero_cost', {}).get('cleared', []))} 个。")
    lines.append(f"- 确定性：两遍 `.tex/.bib` sha256 一致 "
                 f"{totals.get('sha_ok', 0)}/{totals.get('sha_checked', 0)} 个**可判**案例"
                 f"（转换失败案例不参与判定）。")
    if totals.get("zero_cost_unverified"):
        lines.append(f"- ⚠ 零付费断言未验证 {len(totals['zero_cost_unverified'])} 个案例"
                     f"（`--verify-plan` 未成功，非「已确认违规」）：{', '.join(totals['zero_cost_unverified'][:8])}"
                     + ("…" if len(totals["zero_cost_unverified"]) > 8 else ""))
    lines.append("")
    lines.append("## 1. 环境与零付费证据")
    lines.append("")
    zc = run.get("zero_cost", {})
    lines.append(f"- 清空的环境变量：`{', '.join(zc.get('cleared', [])) or '（无）'}`")
    lines.append(f"- 子进程只传：`convert.py --verify-plan --search-refs`（另加来源/作者/输出参数）；"
                 "**未传**任何 `--verify-refs` / `--llm-*` 参数。")
    lines.append(f"- 逐案例断言：{totals.get('zero_cost_verified', 0)}/{totals.get('ran', 0)} 个案例"
                 f"日志确认「预计 AI：0 次」；未验证 {len(totals.get('zero_cost_unverified') or [])} 个"
                 f"（转换/算账未成功，见报告 §9b 明细）"
                 + ("" if totals.get("zero_cost_ok") else "；❌ 有已确认违规，见 P0 `ZERO_COST`"))
    lines.append(f"- 相似度口径：`{run.get('similarity_mode', 'auto')}`"
                 + ("（`--no-similarity` 已跳过）" if run.get("similarity_mode") == "none"
                    else "（`--full-similarity` 全文指标）" if run.get("similarity_mode") == "full"
                    else "（O10 auto：ref+gen > 30000 字符降级行级/前缀快速指标，mode 列标注）"))
    lines.append(f"- 验收档：`{run.get('tier', 'mid')}`（mid=5 期 4–5 min 中间验收；full=5 期 + repo 扫描，最终只跑一次）")
    _sc = run.get("similarity_cache") or {}
    if _sc.get("enabled"):
        lines.append(f"- 相似度缓存：命中 {_sc.get('hits', 0)} / 计算 {_sc.get('misses', 0)}"
                     f"（key = 人工+生成 tex sha + 口径；文件 `{_sc.get('path')}`）")
    if run.get("metadata_disabled"):
        lines.append("- **A2-only 口径**：`--no-metadata` 已启用——manifest 的附加源（如 90-03 的 "
                     "`yalcin puzzle.tex`）**不传给** `convert.py`，用于单独评估引擎通用发现规则；"
                     "若引擎修复不到会退回旧行为（rc=2 等），属预期。")
    lines.append("")
    lines.append("## 2. 确定性（两遍 sha256）")
    lines.append("")
    lines.append("| 案例 | 作者 | .tex before | .tex after | .bib before | .bib after | 一致 |")
    lines.append("|---|---|---|---|---|---|---|")
    for m in ordered:
        sb, sa = m.get("sha_before") or {}, m.get("sha_after") or {}
        sha_mark = "✅" if m.get("sha_ok") is True else ("❌" if m.get("sha_ok") is False else "—（未判）")
        lines.append(f"| {m['key']} | {m['author']} | `{_short(sb.get('tex'))}` | `{_short(sa.get('tex'))}` | "
                     f"`{_short(sb.get('bib'))}` | `{_short(sa.get('bib'))}` | {sha_mark} |")
    lines.append("")
    lines.append("## 3. 总表")
    lines.append("")
    lines.append("「页/文献/引用」= 产物（生成）/人工成品；" 
                 "「悬空/未用」= 生成正文引用但 bib 没有 / bib 有但正文未引用。")
    lines.append("")
    lines.append("| 案例 | 作者 | 通道 | rc | 页(产/人) | !错 | undef C/R | 缺字 | bib(产/人) | "
                 "引用(产/人) | 悬空/未用(产/人) | 节(产/人) | 占位符 | 分级 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for m in ordered:
        if m.get("skipped"):
            lines.append(f"| {m['key']} | {m['author']} | — | SKIP | — | — | — | — | — | — | — | — | — | SKIP |")
            continue
        man = m.get("manual") or {}
        log = m.get("log") or {}
        tex = m.get("tex") or {}
        bib = m.get("bib") or {}
        clo = m.get("closure") or {}
        mclo = man.get("closure") or {}
        lvl = (m.get("level") or "").replace("✅", "✅")
        lines.append(
            f"| {m['key']} | {m['author']} | {m.get('channel', 'auto')} | {_fmt(m.get('convert_rc'))} | "
            f"{_diff_str(m.get('pdf_pages'), man.get('pages'))} | {log.get('n_errors', '—')} | "
            f"{log.get('undefined_citation', '—')}/{log.get('undefined_reference', '—')} | "
            f"{log.get('missing_char', '—')} | {_diff_str(bib.get('entries'), man.get('bib_entries'))} | "
            f"{_diff_str(tex.get('cite'), man.get('cite'))} | "
            f"{_fmt(clo.get('n_dangling'))}/{_fmt(clo.get('n_unused'))}"
            f"（{_fmt(mclo.get('n_unused'))}） | {_diff_str(tex.get('section'), man.get('section'))} | "
            f"{tex.get('placeholder_any', '—')} | **{lvl}** |")
    lines.append("")
    lines.append("## 4. P0 明细")
    lines.append("")
    lines.extend(_render_findings(ordered, "P0"))
    lines.append("## 5. P1 明细")
    lines.append("")
    lines.extend(_render_findings(ordered, "P1"))
    lines.append("## 6. P2 明细")
    lines.append("")
    lines.extend(_render_findings(ordered, "P2"))
    lines.append("## 7. 与人工成品差异汇总")
    lines.append("")
    lines.append("| 案例 | 页数差 | 文献差 | 引用差 | 节差 | 占位符差 | 未引用差 | 一校命中(产/人) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for m in ordered:
        if m.get("skipped"):
            continue
        man = m.get("manual") or {}
        tex, bib = m.get("tex") or {}, m.get("bib") or {}
        clo, mclo = m.get("closure") or {}, man.get("closure") or {}
        p_g, p_m = m.get("pdf_pages"), man.get("pages")
        b_g, b_m = bib.get("entries"), man.get("bib_entries")
        c_g, c_m = tex.get("cite"), man.get("cite")
        s_g, s_m = tex.get("section"), man.get("section")
        ph_g, ph_m = tex.get("placeholder_any"), man.get("placeholders")
        u_g, u_m = clo.get("n_unused"), mclo.get("n_unused")
        f = lambda a, b: _fmt((a - b) if isinstance(a, int) and isinstance(b, int) else None)
        lines.append(f"| {m['key']} | {f(p_g, p_m)} | {f(b_g, b_m)} | {f(c_g, c_m)} | {f(s_g, s_m)} | "
                     f"{f(ph_g, ph_m)} | {f(u_g, u_m)} | "
                     f"{_fmt((m.get('proof') or {}).get('hits'))}/"
                     f"{_fmt((man.get('proof') or {}).get('hits'))} |")
    lines.append("")
    lines.append("## 8. 已知缺口（Phase0/案例 141–144 登记，不 suppress）")
    lines.append("")
    known_rows: list[str] = []
    for m in ordered:
        for f in m.get("findings", []):
            note = is_known(m["key"], f["code"])
            if note:
                known_rows.append(f"- `{m['key']}` {m['author']} **{f['level']} {f['code']}**：{note}")
    lines.extend(known_rows or ["- （本批未命中已登记缺口）"])
    lines.append("")
    lines.append("## 9. 每案例附录")
    lines.append("")
    for m in ordered:
        if m.get("skipped"):
            lines.append(f"### {m['key']} {m['author']} —— SKIP：{m['skipped']}")
            lines.append("")
            continue
        log = m.get("log") or {}
        lines.append(f"### {m['key']} {m['author']}（{m.get('level')}，{m.get('elapsed_s')}s）")
        lines.append("")
        lines.append(f"- 源：`{m.get('source')}`（{m.get('source_note', '')}）")
        meta = m.get("metadata") or {}
        if meta.get("declared"):
            if meta.get("found"):
                lines.append(f"- 附加源（--metadata）：`{meta.get('path')}`")
            else:
                lines.append(f"- {meta.get('note')}")
        if (m.get("doc_convert") or {}).get("needed"):
            lines.append(f"- .doc→docx：{'✅' if m['doc_convert'].get('ok') else '❌'} "
                         f"`{m['doc_convert'].get('path', '')}`")
        lines.append(f"- 产物：`{m.get('product_dir')}`（rc={m.get('convert_rc')}，"
                     f"PDF {_fmt(m.get('pdf_pages'))} 页，编译 "
                     f"{'✅' if (m.get('convert') or {}).get('compile_ok') else '❌/未知'}）")
        lines.append(f"- 日志：`{run.get('out_dir')}/logs/convert/{m['key']}_pass1.log`、"
                     f"`..._pass2_plan.log`")
        lines.append(f"- 判据：! 错 {log.get('n_errors', '—')}；undef cite/ref "
                     f"{log.get('undefined_citation', '—')}/{log.get('undefined_reference', '—')}；"
                     f"缺字 {log.get('missing_char', '—')}；overfull {log.get('overfull', '—')}；"
                     f"biber ERROR {(m.get('biber') or {}).get('errors', '—')}")
        proof = m.get("proof") or {}
        lines.append(f"- 一校（生成）：命中 {_fmt(proof.get('hits'))}（可自动改 {_fmt(proof.get('fixable'))}，"
                     f"规则 {json.dumps(proof.get('rules') or {}, ensure_ascii=False)}）")
        if m.get("manual"):
            man = m["manual"]
            lines.append(f"- 人工成品：`{man.get('dir')}`（{_fmt(man.get('pages'))} 页，"
                         f"{_fmt(man.get('bib_entries'))} 条 bib，一校命中 "
                         f"{_fmt((man.get('proof') or {}).get('hits'))}）")
        if m.get("findings"):
            lines.append("- 判级：" + "；".join(f"{f['level']} {f['code']}" for f in m["findings"]))
        lines.append("")
    dash = run.get("dashboard") or []
    if dash:
        bench_ch = (run.get("bench_by_channel") or {})
        if not bench_ch:
            bench_ch = {"word": sum(1 for m in ordered if (m.get("channel") == "word")),
                        "latex": sum(1 for m in ordered if (m.get("channel") == "latex"))}
        lines.append("## 9b. 全量基准看板（P0/P1/P2 × 根因聚类）")
        lines.append("")
        lines.append(f"- 篇数 **{totals.get('ran', 0)}**（SKIP {totals.get('skipped', 0)}）；"
                     f"通道 Word {bench_ch.get('word', '—')} / LaTeX {bench_ch.get('latex', '—')}；"
                     f"**P0 {totals.get('p0', 0)} / P1 {totals.get('p1', 0)} / P2 {totals.get('p2', 0)}**；"
                     f"总耗时 {run.get('elapsed_total_s', '—')}s，吞吐 {run.get('throughput_per_min', '—')} 篇/分钟")
        lines.append("")
        rs = run.get("repo_scan") or {}
        if rs:
            lines.append(f"- repo 扫描：期 {rs.get('issues')} / 篇 {rs.get('articles')} → 可测 "
                         f"**{rs.get('cases')}**；跳过（无人工成品 tex {rs.get('skipped_no_manual_tex')} / "
                         f"无源稿 {rs.get('skipped_no_source')} / 源=成品 {rs.get('skipped_manual_is_source')}）")
            lines.append("")
        lines.append("| 排名 | 级别 | 根因聚类 | 影响篇数 | 条数 | 建议承接 | 预估 | 优先级分 | 签名/样例 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in dash:
            lines.append(f"| {r['rank']} | **{r['level']}** | {r['cluster']}（`{r['cluster_code']}`） | "
                         f"{r['cases_count']} | {r['findings_count']} | {r['owner']} | {r['effort']} | "
                         f"{r['priority']} | {(r['signatures'] or r['examples'])[:160]} |")
        lines.append("")
        lines.append("## 9c. 前 10 根因与修复优先级建议")
        lines.append("")
        for r in dash[:10]:
            lines.append(f"{r['rank']}. **[{r['level']}] {r['cluster']}**（{r['cases_count']} 篇 / "
                         f"{r['findings_count']} 条，优先级分 {r['priority']}）→ 建议 **{r['owner']}**"
                         f"（预估 {r['effort']}）；证据：{(r['signatures'] or r['examples'])[:220]}")
        lines.append("")
        ranked = []
        for m in ordered:
            if m.get("skipped"):
                continue
            p0 = sum(1 for f in m.get("findings") or [] if f["level"] == "P0")
            p1 = sum(1 for f in m.get("findings") or [] if f["level"] == "P1")
            p2 = sum(1 for f in m.get("findings") or [] if f["level"] == "P2")
            ranked.append((3 * p0 + 2 * p1 + p2, m, p0, p1, p2))
        ranked.sort(key=lambda x: (-x[0], x[1]["key"]))
        lines.append("### 受影响最大的篇（严重度加权 Top 10）")
        lines.append("")
        lines.append("| 案例 | 作者 | 级别 | P0/P1/P2 | 加权分 | 关键判据 |")
        lines.append("|---|---|---|---|---|---|")
        for score, m, p0, p1, p2 in ranked[:10]:
            man = m.get("manual") or {}
            tex = m.get("tex") or {}
            log = m.get("log") or {}
            codes = "；".join(f"{f['level']}:{f['code']}" for f in m.get("findings") or [])[:140]
            lines.append(f"| {m['key']} | {m['author']} | {m.get('level')} | {p0}/{p1}/{p2} | {score} | "
                         f"rc={m.get('convert_rc')} 页 {_fmt(m.get('pdf_pages'))}/{_fmt(man.get('pages'))} "
                         f"bib {_fmt((m.get('bib') or {}).get('entries'))}/{_fmt(man.get('bib_entries'))} "
                         f"缺字 {_fmt(log.get('missing_char'))}；{codes} |")
        lines.append("")
    lines.append("## 10. 复现方式")
    lines.append("")
    lines.append("```bash")
    lines.append("PYTHONPATH=Engine/vendor /opt/homebrew/bin/python3.14 tools/batch_case_test.py \\")
    lines.append(f"  --out {run.get('out_dir')}")
    lines.append("```")
    lines.append("")
    lines.append("*本报告由 `tools/batch_case_test.py` 生成；只读输入、零付费、产物只写 /tmp。*")
    return "\n".join(lines) + "\n"


def _short(sha: str | None) -> str:
    return (sha or "—")[:12]


def _render_findings(ordered: list[dict], level: str) -> list[str]:
    rows = []
    for m in ordered:
        for f in m.get("findings", []):
            if f["level"] != level:
                continue
            note = is_known(m["key"], f["code"])
            rows.append(f"| {m['key']} | {m['author']} | `{f['code']}` | {f['detail']} | "
                        f"{'✅ 已知：' + note if note else '—'} |")
    if not rows:
        return ["（无）", ""]
    return (["| 案例 | 作者 | 代码 | 说明 | 已知? |", "|---|---|---|---|---|"]
            + rows + [""])


# ─────────────────────────────────────────────────────────────────────────────
# 汇总 / 入口
# ─────────────────────────────────────────────────────────────────────────────


def build_summary(run: dict) -> dict:
    cases = [run["cases"][k] for k in run.get("case_order", []) if k in run["cases"]]
    findings = [(m["key"], f) for m in cases for f in m.get("findings", [])]
    totals = {
        "total": len(run.get("case_order", [])),
        "ran": sum(1 for m in cases if not m.get("skipped")),
        "skipped": sum(1 for m in cases if m.get("skipped")),
        "p0": sum(1 for _k, f in findings if f["level"] == "P0"),
        "p1": sum(1 for _k, f in findings if f["level"] == "P1"),
        "p2": sum(1 for _k, f in findings if f["level"] == "P2"),
        "p0_known": sum(1 for k, f in findings if f["level"] == "P0" and is_known(k, f["code"])),
        "p0_new": sum(1 for k, f in findings if f["level"] == "P0" and not is_known(k, f["code"])),
        "sha_ok": sum(1 for m in cases if m.get("sha_ok") is True),
        "sha_checked": sum(1 for m in cases if m.get("sha_ok") is not None),
        "zero_cost_ok": not any((m.get("plan") or {}).get("ai_zero") is False
                                for m in cases if not m.get("skipped")),
        "zero_cost_verified": sum(1 for m in cases if (m.get("plan") or {}).get("ai_zero") is True),
        "zero_cost_unverified": [m["key"] for m in cases
                                 if not m.get("skipped")
                                 and (m.get("plan") or {}).get("ai_zero") is None],
    }
    run["totals"] = totals
    return totals


def git_head() -> str:
    try:
        p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        return p.stdout.strip() or "—"
    except Exception:
        return "—"


def git_fingerprint() -> dict:
    """仓库快照：HEAD + status 摘要（基线期间被并行改动 → 报告要能发现）。"""
    out = {"head": git_head(), "status_sha": None, "status_lines": 0}
    try:
        p = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        text = p.stdout
        out["status_sha"] = hashlib.sha256(text.encode()).hexdigest()[:16]
        out["status_lines"] = len([ln for ln in text.splitlines() if ln.strip()])
    except Exception:
        pass
    return out


def run_batch(args: argparse.Namespace) -> int:
    started = time.time()
    out_root = Path(args.out) if args.out else (DEFAULT_OUT_ROOT / (args.label or _stamp()))
    out_root.mkdir(parents=True, exist_ok=True)
    py = args.python or sys.executable

    # 零付费：启动即清空付费相关环境变量
    env, cleared = sanitize_env()
    if cleared:
        print(f"[零点] 已清空付费相关环境变量：{', '.join(cleared)}")
    repo_start = git_fingerprint()

    # 工作区 / manifest
    workspaces = dict(DEFAULT_WORKSPACES)
    for item in args.workspace or []:
        issue, _, path = item.partition("=")
        if not path:
            print(f"[用法] --workspace 需要 ISSUE=DIR：{item}", file=sys.stderr)
            return 2
        workspaces[issue] = Path(path).expanduser()
    man_repo = Path(args.manuscripts).expanduser() if args.manuscripts else MANUSCRIPTS_DEFAULT
    man_roots = {issue: man_repo / sub for issue, sub in MANUAL_ROOTS.items()}

    # O12 分层验收：tier 必须在 load_cases / repo 扫描之前解析（决定默认 5 期 与 full 的 repo 扫描）
    tier = getattr(args, "tier", "mid") or "mid"
    if tier not in ("mid", "full"):
        print(f"[用法] --tier 只接受 mid/full：{tier}", file=sys.stderr)
        return 2
    if not getattr(args, "issues", ""):
        args.issues = tier_issue_arg(tier)
    if tier_repo_scan(tier, bool(getattr(args, "repo_scan", False))):
        args.repo_scan = True

    cases = load_cases(args)
    repo_stats: dict = {}
    if getattr(args, "repo_scan", False) and not args.case:
        repo_cases, repo_stats = scan_repo_cases(man_repo)
        cases.extend(repo_cases)
        print(f"[repo 扫描] 期 {repo_stats['issues']} / 篇 {repo_stats['articles']} → "
              f"可测 {repo_stats['cases']}（跳过：无人工成品 tex {repo_stats['skipped_no_manual_tex']}、"
              f"无源稿 {repo_stats['skipped_no_source']}、源=成品 {repo_stats['skipped_manual_is_source']}）")
    if not cases:
        print("[用法] 没有可跑的案例（检查 --issues / --manifest / --case）", file=sys.stderr)
        return 2
    # 期 90 → 87 → 88 → 89 → repo（内置顺序已满足；--manifest 时也按 issue 顺序稳定排序）
    order = {issue: i for i, issue in enumerate(DEFAULT_ISSUE_ORDER)}
    cases.sort(key=lambda c: (order.get(c.issue, len(order)), c.key))

    if getattr(args, "inventory", False):
        est = estimate_bench(cases)
        print(f"[盘点] 可测 {est['cases']} 篇；通道 Word {est['by_channel']['word']} / "
              f"LaTeX {est['by_channel']['latex']} / 未知 {est['by_channel']['other']}")
        print(f"[盘点] 按 issue：{est['by_issue']}")
        print(f"[盘点] 预计耗时 {est['est_seconds']}s ≈ {est['est_minutes']} min"
              f"（按 {est['assumption_sec_per_case']}s/篇串行估算，含转换+编译+算账+一校/回读+人工对照）")
        if repo_stats:
            print(f"[盘点] repo 扫描统计：{json.dumps(repo_stats, ensure_ascii=False)}")
        return 0
    if getattr(args, "limit", 0):
        cases = cases[:args.limit]
        print(f"[限制] 只跑前 {len(cases)} 个案例（--limit）")

    metadata_disabled = bool(getattr(args, "no_metadata", False))
    sim_mode = ("none" if getattr(args, "no_similarity", False)
                else ("full" if getattr(args, "full_similarity", False) else "auto"))
    sim_cache: dict | None = None
    sim_cache_path = Path(getattr(args, "similarity_cache", "") or (out_root / "similarity-cache.json"))
    if sim_mode != "none" and not getattr(args, "no_similarity_cache", False):
        try:
            sim_cache = json.loads(sim_cache_path.read_text(encoding="utf-8")) if sim_cache_path.is_file() else {}
        except (OSError, ValueError):
            sim_cache = {}
    run: dict = {
        "run_id": args.label or out_root.name,
        "metadata_disabled": metadata_disabled, "similarity_mode": sim_mode,
        "tier": tier, "similarity_cache_path": str(sim_cache_path) if sim_cache is not None else "",
        "started": now_iso(), "out_dir": str(out_root), "python": py,
        "engine_version": ENGINE_VERSION, "git_head": repo_start["head"],
        "repo_start": repo_start, "workspaces": {k: str(v) for k, v in workspaces.items()},
        "manuscripts": str(man_repo), "repo_scan": repo_stats,
        "zero_cost": {"cleared": cleared, "violations": []},
        "cases": {}, "case_order": [c.key for c in cases],
        "args": vars(args),
    }
    print(f"[批次] {len(cases)} 个案例 → {out_root}")
    print("[批次] 顺序：" + " → ".join(
        f"{c.issue}/{c.key.split('-', 1)[-1]} {c.author}" for c in cases))

    for i, case in enumerate(cases, 1):
        ws = Path(case.workspace) if case.workspace else workspaces.get(case.issue)
        mroot = Path(case.manual_root) if case.manual_root else man_roots.get(case.issue, man_repo)
        print(f"[{i}/{len(cases)}] {case.key} {case.author} …", flush=True)
        if ws is None:
            m = {"key": case.key, "issue": case.issue, "author": case.author,
                 "skipped": f"没有工作区映射（--workspace {case.issue}=DIR）", "level": "SKIP",
                 "findings": [], "elapsed_s": 0.0}
        else:
            m = run_case(case, ws, mroot, out_root, py, env,
                         argv_timeout(args.timeout), compare=not args.no_compare,
                         use_metadata=not metadata_disabled, similarity=sim_mode,
                         sim_cache=sim_cache)
            m["channel"] = "latex" if str(m.get("source", "")).endswith(".tex") else "word"
            if (m.get("plan") or {}).get("ai_zero") is False and not m.get("skipped"):
                run["zero_cost"]["violations"].append(case.key)
        run["cases"][case.key] = m
        if m.get("skipped"):
            print(f"    SKIP: {m['skipped']}")
        else:
            man = m.get("manual") or {}
            print(f"    {m.get('level')}  rc={m.get('convert_rc')}  pdf={m.get('pdf_pages')}"
                  f"/{man.get('pages')}  bib={((m.get('bib') or {}).get('entries'))}"
                  f"/{man.get('bib_entries')}  缺字={((m.get('log') or {}).get('missing_char'))}"
                  f"  {m.get('elapsed_s')}s  findings={len(m.get('findings') or [])}")

    run["finished"] = now_iso()
    run["repo_end"] = git_fingerprint()
    totals = build_summary(run)
    hit = sum(1 for m in run["cases"].values() if m.get("sim_cache") == "hit")
    miss = sum(1 for m in run["cases"].values() if m.get("sim_cache") == "miss")
    run["similarity_cache"] = {"path": str(sim_cache_path) if sim_cache is not None else "",
                               "hits": hit, "misses": miss,
                               "enabled": sim_cache is not None}
    if sim_cache is not None:
        try:
            sim_cache_path.parent.mkdir(parents=True, exist_ok=True)
            sim_cache_path.write_text(json.dumps(sim_cache, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            print(f"[缓存] 写入失败（不影响结果）：{exc}", file=sys.stderr)
    run["elapsed_total_s"] = round(time.time() - started, 1)
    run["throughput_per_min"] = round(totals["ran"] / max(run["elapsed_total_s"] / 60, 0.01), 1)
    run["dashboard"] = build_dashboard(run)
    if run["repo_start"] != run["repo_end"]:
        print("[⚠] 基线期间仓库状态发生变化（git status/HEAD），报告已记录；确定性可能受影响")

    summary = {k: v for k, v in run.items() if k not in ("cases",)}
    summary["cases"] = run["cases"]
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    write_dashboard_csv(out_root / "dashboard.csv", run["dashboard"])
    write_cases_csv(out_root / "cases.csv", run)
    report = render_report(run)
    (out_root / "report.md").write_text(report, encoding="utf-8")

    elapsed = time.time() - started
    print()
    print("=" * 72)
    print(f"完成：{totals['ran']}/{totals['total']} 案例（SKIP {totals['skipped']}）；"
          f"P0 {totals['p0']}（新增 {totals['p0_new']}，已知 {totals['p0_known']}）/"
          f"P1 {totals['p1']} / P2 {totals['p2']}；"
          f"sha 一致 {totals['sha_ok']}/{totals.get('sha_checked', 0)}（可判案例）；"
          f"零付费 {'✅' if totals['zero_cost_ok'] else '❌'}"
          f"（已验证 {totals.get('zero_cost_verified', 0)}，未验证 {len(totals.get('zero_cost_unverified') or [])}）")
    print(f"耗时 {elapsed:.1f}s；报告 {out_root / 'report.md'}；数据 {out_root / 'summary.json'}")
    return 0


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M")


def argv_timeout(v: float) -> float:
    return v if v and v > 0 else 600.0


def load_cases(args: argparse.Namespace) -> list[CaseSpec]:
    """内置清单 / 外部 manifest，按 --issues / --case 过滤。"""
    if args.manifest:
        raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        items = raw.get("cases", []) if isinstance(raw, dict) else raw
        cases = []
        for it in items:
            cases.append(CaseSpec(
                key=str(it["key"]), issue=str(it.get("issue", "")),
                author=str(it["author"]), author_dir=str(it.get("author_dir") or it["author"]),
                manual_dir=str(it.get("manual_dir", "")),
                manual_bib=str(it.get("manual_bib", "reference.bib")),
                source=str(it.get("source", "")),
                metadata=str(it.get("metadata", "")),
                extra_args=tuple(it.get("extra_args") or ()),
            ))
    else:
        cases = default_cases()
    wanted_issues = set((args.issues or "").split(",")) - {""}
    if wanted_issues:
        cases = [c for c in cases if c.issue in wanted_issues]
    if args.case:
        filt = args.case.strip()
        cases = [c for c in cases
                 if c.key == filt or f"{c.issue}/{c.key.split('-', 1)[-1]}" == filt
                 or c.author == filt]
    return cases


# ─────────────────────────────────────────────────────────────────────────────
# 自检（--self-test）：不读真实稿、不跑 xelatex，秒级验证关键判据
# ─────────────────────────────────────────────────────────────────────────────


def run_self_test() -> int:
    print("batch_case_test --self-test")
    fails: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            fails.append(name)
        print(f"  [{'OK' if cond else 'FAIL'}] {name}")

    env, cleared = sanitize_env({"SLCN_LLM_KEY": "x", "SLCN_SEARCH_KEY": "y",
                                 "HOME": "/tmp", "PYTHONPATH": ""})
    check("零付费：清空 SLCN_LLM_KEY/SLCN_SEARCH_KEY", "SLCN_LLM_KEY" not in env
          and "SLCN_SEARCH_KEY" not in env and "HOME" in env)
    check("零付费：PYTHONPATH 注入 Engine+vendor",
          str(ENGINE) in env["PYTHONPATH"] and str(VENDOR) in env["PYTHONPATH"])

    clean = {"convert_rc": 0, "pdf_pages": 16,
             "log": {"n_errors": 0, "undefined_citation": 0, "undefined_reference": 0,
                     "missing_char": 0},
             "biber": {"errors": 0}, "sha_ok": True, "plan": {"ai_zero": True, "ai": 0, "rc": 0},
             "closure": {"n_dangling": 0, "n_unused": 0}, "tex": {"cite": 20, "section": 5,
                                                                  "placeholder_any": 0},
             "bib": {"entries": 20}}
    man = {"pages": 16, "bib_entries": 20, "cite": 20, "section": 5, "placeholders": 0,
           "closure": {"n_unused": 0}, "bib_must": 0, "proof": {"rules": {}}}
    check("干净案例无 finding", grade_case(clean, man) == [])
    bad = dict(clean, convert_rc=2)
    check("rc≠0 → P0 CONVERT_RC",
          any(f.level == "P0" and f.code == "CONVERT_RC" for f in grade_case(bad, man)))
    check("无 PDF → P0 NO_PDF",
          any(f.code == "NO_PDF" for f in grade_case(dict(clean, pdf_pages=None), man)))
    check("缺字符 → P0 MISSING_CHAR",
          any(f.code == "MISSING_CHAR" for f in grade_case(
              dict(clean, log={**clean["log"], "missing_char": 3}), man)))
    check("sha 不一致 → P0 NONDETERMINISTIC",
          any(f.code == "NONDETERMINISTIC" for f in grade_case(dict(clean, sha_ok=False), man)))
    check("零付费断言失败 → P0 ZERO_COST",
          any(f.code == "ZERO_COST" for f in grade_case(
              dict(clean, plan={"ai_zero": False, "ai": 3, "rc": 0}), man)))
    check("页数差 1 → P1 / 差 2 → P0",
          any(f.level == "P1" and f.code == "PAGE_DIFF" for f in grade_case(
              dict(clean, pdf_pages=17), man))
          and any(f.level == "P0" and f.code == "PAGE_DIFF" for f in grade_case(
              dict(clean, pdf_pages=18), man)))
    check("文献差 3 → P0 BIB_COUNT_DIFF",
          any(f.level == "P0" and f.code == "BIB_COUNT_DIFF" for f in grade_case(
              dict(clean, bib={"entries": 23}), man)))
    check("正文 0 引用但 bib>0 → P0 NO_CITES",
          any(f.code == "NO_CITES" for f in grade_case(
              dict(clean, tex={**clean["tex"], "cite": 0}), man)))
    check("占位符>0 → P1 PLACEHOLDER",
          any(f.level == "P1" and f.code == "PLACEHOLDER" for f in grade_case(
              dict(clean, tex={**clean["tex"], "placeholder_any": 3}), man)))
    check("解析：预计 AI：0 次 → (True, 0)", parse_plan_ai("预计 AI：0 次（未配置 LLM") == (True, 0))
    check("解析：预计 AI：≤ 3 次 → (False, 3)",
          parse_plan_ai("预计 AI：≤ 3 次调用") == (False, 3))
    check("解析：xelatex 日志判据",
          (lambda s: s["n_errors"] == 1 and s["undefined_citation"] == 1
           and s["missing_char"] == 2 and s["log_pages"] == 14)(log_stats(
              "! Undefined control sequence.\nLaTeX Warning: Citation 'a' on page 1 undefined.\n"
              "Missing character: There is no ⊩ (U+22A9).\nMissing character: There is no 是.\n"
              "Output written on X.pdf (14 pages).")))
    check("解析：一校报告规则分布", parse_proofcheck_md(
        "| `ZH-06` | **1** | 顿号 | x |\n| `ZH-08` | **3** | 短杠 | y |\n"
        "**合计命中 4 处。**")["total"] == 4)
    check("解析：回读校验结论", parse_checkbib_md(
        "**结论：42 处问题 / 29 处提示** —— x（解析出 17 条条目）")["problems"] == 42)
    check("闭合：悬空/未用", citations_closure(
        r"\parencite{A} \parencite{B}", "@article{A,\n title={t}\n}") == {
            "n_cited": 2, "n_bib": 1, "dangling": ["B"], "unused": [],
            "n_dangling": 1, "n_unused": 0})
    dc = default_cases()
    check("默认清单：90(10)→87(9)→88(7)→89(5)→86(10) 共 41 篇",
          [c.issue for c in dc[:10]] == ["90"] * 10
          and [c.issue for c in dc[10:19]] == ["87"] * 9
          and [c.issue for c in dc[19:26]] == ["88"] * 7
          and [c.issue for c in dc[26:31]] == ["89"] * 5
          and [c.issue for c in dc[31:]] == ["86"] * 10
          and len(dc) == 41)
    check("期 87 子案例编号 ↔ 作者映射（1=Kun_Xing … 9=Wei_Chen）",
          [c.author for c in default_cases() if c.issue == "87"] ==
          ["Kun_Xing", "Wenjing_Du", "Fayang_Ye", "Pengfei_Song", "Ruixue_Tang",
           "Guangwu_Feng", "Ran_Ren", "Runcheng_Liang", "Wei_Chen"])
    check("已知缺口标注不 suppress：87-5 NO_CITES 判 P0 且标已知",
          is_known("87-5", "NO_CITES") is not None
          and any(f.level == "P0" and f.code == "NO_CITES" for f in grade_case(
              dict(clean, tex={**clean["tex"], "cite": 0}), man)))
    check("默认 out 根不在仓库/Desktop",
          DEFAULT_OUT_ROOT == Path("/tmp/batch_case_test")
          and not str(DEFAULT_OUT_ROOT).startswith(str(ROOT)))

    d3 = next(c for c in default_cases() if c.key == "90-03")
    check("90-03 manifest 声明附加源 metadata=yalcin puzzle.tex",
          d3.metadata == "yalcin puzzle.tex"
          and sum(1 for c in default_cases() if c.metadata) == 1)
    with tempfile.TemporaryDirectory(prefix="slcn_selftest_") as td:
        ws = Path(td)
        (ws / "Liang_Zhang").mkdir(parents=True, exist_ok=True)
        meta = ws / "Liang_Zhang" / "yalcin puzzle.tex"
        meta.write_text("\\documentclass{x}", encoding="utf-8")
        src = ws / "Liang_Zhang" / "main.tex"
        src.write_text("body", encoding="utf-8")
        spec3 = CaseSpec(key="t-90-03", issue="90", author="Liang_Zhang",
                         author_dir="Liang_Zhang", metadata="yalcin puzzle.tex")
        args, info = convert_common_args(spec3, src, ws / "out", ws)
        check("附加源：文件存在 → 命令含 --metadata <绝对路径>",
              "--metadata" in args and args[args.index("--metadata") + 1] == str(meta)
              and info["found"])
        args2, info2 = convert_common_args(spec3, src, ws / "out", ws, use_metadata=False)
        check("--no-metadata：A2-only 口径不传 --metadata",
              "--metadata" not in args2 and info2.get("disabled") is True)
        spec_bad = CaseSpec(key="t-bad", issue="90", author="X", author_dir="X",
                            metadata="nope.tex")
        args3, info3 = convert_common_args(spec_bad, src, ws / "out", ws)
        check("附加源文件不存在 → 安全回退（不传、note 写明候选）",
              "--metadata" not in args3 and info3["fallback"] is True
              and "回退" in info3["note"])

    _dash = build_dashboard(
        {"case_order": ["a", "b"],
         "cases": {
             "a": {"convert_rc": 2,
                   "log": {"errors": ["Undefined control sequence."]},
                   "findings": [{"level": "P0", "code": "CONVERT_RC", "detail": "rc=2"},
                                {"level": "P1", "code": "PAGE_DIFF", "detail": "页差 1"}]},
             "b": {"convert_rc": 0, "log": {},
                   "findings": [{"level": "P1", "code": "PAGE_DIFF", "detail": "页差 1"}]},
         }})
    check("输出名 sanitize / sha 判定 / 零付费判定（失败案例不算违规）",
          sanitize_output_name("Liwu Rong") == "Liwu_Rong"
          and sanitize_output_name("Bo_Chen") == "Bo_Chen"
          and sanitize_output_name("1st Case") == "A_1st_Case"
          and sha_verdict(0, True, True, {"tex": "a", "bib": "b"}, {"tex": "a", "bib": "b"}) is True
          and sha_verdict(2, False, False, {}, {}) is None
          and plan_ai_status(0, 0) is True
          and plan_ai_status(0, 3) is False
          and plan_ai_status(2, None) is None)
    check("O10 相似度：口径选择 + fast/full/none 行为",
          similarity_choice("aa", "bb", "auto") == "full"
          and similarity_choice("甲" * 20000, "乙" * 20000, "auto") == "fast"
          and similarity_choice("aa", "bb", "full") == "full"
          and similarity_choice("aa", "bb", "none") == "none"
          and compute_similarity("x", "x", "none") == {"mode": "skipped"}
          and compute_similarity("a\n" * 20000, "a\n" * 20000, "auto")["mode"] == "fast")
    _sim_cache: dict = {}
    _r1, _h1 = similarity_cached("甲。", "甲。", "auto", _sim_cache)
    _r2, _h2 = similarity_cached("甲。", "甲。", "auto", _sim_cache)
    _r3, _h3 = similarity_cached("甲。乙。", "甲。", "auto", _sim_cache)
    check("O12 分层验收：tier 解析 + 相似度缓存命中/失效",
          tier_issue_arg("mid") == "86,87,88,89,90"
          and tier_repo_scan("mid", False) is False
          and tier_repo_scan("full", False) is True
          and tier_repo_scan("mid", True) is True
          and similarity_cache_key("m", "g", "auto") == similarity_cache_key("m", "g", "auto")
          and similarity_cache_key("m", "g", "auto") != similarity_cache_key("m", "g", "full")
          and _h1 is False and _h2 is True and _h3 is False)
    check("benchmark：issue code 解析 + dashboard 聚合 + 估计函数",
          _parse_issue_code("2026年第2期(87)") == "87"
          and _parse_issue_code("2022年第6A期(67A)") == "67A"
          and estimate_bench([CaseSpec(key="x", issue="repo", author="A", author_dir=".",
                                       source="/tmp/a.docx")])["est_minutes"] == round(8.5 / 60, 1)
          and [r["level"] for r in _dash][:2] == ["P0", "P1"])
    print()
    print(f"失败 {len(fails)} 项" + (f"：{fails}" if fails else "（全部通过）"))
    return 1 if fails else 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="batch_case_test.py",
        description="批量实例测试：转换→编译→两遍 sha256→一校/回读→人工对照→P0/P1/P2 报告"
                    "（零付费、只读输入、产物只写 /tmp）",
    )
    ap.add_argument("--tier", choices=("mid", "full"), default="mid",
                    help="验收档：mid=5 期（默认，中间验收 4–5 min）；full=5 期+repo 扫描（最终只跑一次）")
    ap.add_argument("--similarity-cache", default="",
                    help="相似度缓存 JSON 路径（默认 <out>/similarity-cache.json）")
    ap.add_argument("--no-similarity-cache", action="store_true", help="禁用相似度缓存")
    ap.add_argument("--issues", default="",
                    help="只跑这些期（逗号分隔；默认 90,87,88,89）")
    ap.add_argument("--case", default="",
                    help="只跑一个案例：`90/05` 或 `87-3` 或作者名（如 Beibei_Gao）")
    ap.add_argument("--workspace", action="append", metavar="ISSUE=DIR",
                    help="覆盖工作区路径（可重复），如 --workspace 90=/path/macCaseTest")
    ap.add_argument("--manifest", default="",
                    help="外部 JSON manifest（缺省用内置期 87/90 清单）")
    ap.add_argument("--manuscripts", default="",
                    help="稿件仓根目录（默认同级 Studies_in_Logic_Database；只读）")
    ap.add_argument("--out", default="",
                    help="输出根目录（默认 /tmp/batch_case_test/<时间戳>）")
    ap.add_argument("--label", default="", help="run 标签（默认用输出目录名）")
    ap.add_argument("--python", default="", help="子进程解释器（默认当前解释器）")
    ap.add_argument("--timeout", type=float, default=600.0, help="单案例子进程超时秒（默认 600）")
    ap.add_argument("--no-compare", action="store_true", help="不读稿件仓人工成品（只做绝对判据）")
    sim_g = ap.add_mutually_exclusive_group()
    sim_g.add_argument("--no-similarity", action="store_true",
                       help="跳过相似度计算（最快；相似度列留空）")
    sim_g.add_argument("--full-similarity", action="store_true",
                       help="强制全文 slcn.score 指标（最慢，仅校准时用）")
    ap.add_argument("--no-metadata", action="store_true",
                    help="忽略 manifest 的附加源（--metadata），模拟仅靠引擎通用发现（A2-only 口径）")
    ap.add_argument("--repo-scan", action="store_true",
                    help="只读扫描稿件仓，把「有源稿+有人工成品 tex」的期篇并入全量基准")
    ap.add_argument("--inventory", action="store_true",
                    help="只盘点篇数/通道/预计耗时（不跑转换；重活窗口申请用）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个案例（冒烟用）")
    ap.add_argument("--self-test", action="store_true", help="跑内置自检（不读真实稿）")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.self_test:
        return run_self_test()
    try:
        return run_batch(args)
    except KeyboardInterrupt:
        print("\n[中断] 用户中断，已写出的 /tmp 产物保留", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
