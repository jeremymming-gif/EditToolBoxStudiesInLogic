"""B-288：任务书 v2 的**任务卡**（放在任务书最前，给编辑和他们的 AI 当「第一页」）。

任务卡回答三个问题，都不依赖具体稿件内容：

1. **这一份是什么任务、版本/规则集是哪个**（TASK-ID + 版本 + 规则摘要）——任务书被转发、
   改存、甚至只截了前几页时，编辑还能对上是哪一次生成；
2. **不能做什么**（5 条铁律）——尤其「不改写正文」「不用表格」「数学区不报」；
3. **不用报什么**（29 条免报清单）——逐条由 `slcn.ruleset` 从 `proofcheck.RULES` 现生成，
   本模块**不存第二份规则表**。

TASK-ID 的 8 位十六进制**独立计算**，不复用 `coop.build_state()` 里的 `fingerprint`：
那个指纹管的是「两段式协同的参数有没有变」，而 TASK-ID 要能区分**稿件内容/语种/核实范围**
（同一份稿件换 `--verify-max` 应得到不同 ID）。输入见 `task_id()` 的文档。

卡总长有硬上限（≤60 行，`tests/test_b288_taskcard.py` 断言）：太长会挤掉真正的任务书，
编辑的 AI 也更可能只读前半。加内容前先想清楚删哪一行。
"""

from __future__ import annotations

import hashlib
import re

#: 任务书版本（B-288 起为 2；任务卡头与 `state["taskbook_version"]` 同源）。
TASKBOOK_VERSION = 2

#: 参与 TASK-ID 的 schema 串（**不是** `coop.SCHEMA_VERSION`：那个管状态 JSON 的兼容，
#: 与任务书结构无关；两者分开才能「任务书升版但状态 schema 仍 1」）。
TASKBOOK_SCHEMA = "taskbook.v2"

#: 卡内 TASK-ID 的固定标记（`extract_task_id()` 与 `render_card()` 共用）。
TASK_ID_LABEL_ZH = "**TASK-ID**："
TASK_ID_LABEL_EN = "**TASK-ID**:"

_TASK_ID_RE = re.compile(r"\*\*TASK-ID\*\*[：:]\s*`([^`\n]+)`")


def task_id(source_sha256: str, engine_version: str, ruleset_id: str,
            schema: str, kind: str, lang: str, scope_keys,
            label: str = "") -> str:
    """算 TASK-ID：`<KIND>-<label>-<sha256 前 8 位>`（`label` 省略时两段）。

    哈希输入（顺序固定、`\\x1f` 分隔，全部字符串化后再算）：

        source_sha256 | engine_version | ruleset_id | schema | kind | lang |
        ",".join(sorted(scope_keys))

    `scope_keys` 是这次核实的条目键（全量时调用方传 `*`）；调用方**还应把 `verify-max`
    等影响范围的参数拼进去**——同一份 bib 换 `--verify-max` 必须换 ID。
    为什么不复用 `params["fingerprint"]`：指纹按参数表算，**不含稿件 sha256 与语种**，
    两份不同稿件只要参数相同就会撞 ID，任务书转发后就分不清了。
    """
    keys = ",".join(sorted(str(k) for k in (scope_keys or ())))
    payload = "\x1f".join((
        str(source_sha256), str(engine_version), str(ruleset_id), str(schema),
        str(kind), str(lang), keys,
    ))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    parts = [str(kind or "BIB").upper()]
    lab = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(label or "")).strip("_")
    if lab:
        parts.append(lab)
    parts.append(digest)
    return "-".join(parts)


def extract_task_id(text: str) -> str:
    """从任务书正文里取回 TASK-ID（任务书由 `refmark.build()` 写，卡格式固定）。

    为什么不让调用方传：`run_coop_taskbook()` 在 `convert.py` 里被调用，而门面
    （`convert.py`）这一批**不许改**；任务书里已有唯一 ID，读回来比重复算一遍稳
    （重复算要再拼一遍 scope/verify-max，反而容易与写卡时不一致）。
    """
    m = _TASK_ID_RE.search(text or "")
    return m.group(1) if m else ""


# ── 卡正文 ────────────────────────────────────────────────────────────────

_IRON_ZH = (
    "**不改写正文**、不润色、不增删；只报告，不替作者决定。",
    "**不用表格**；回复只用纯文本与代码块。",
    "定位一律 **Exact Match**：给原样片段，不改字、不省略、不加省略号。",
    "**数学区一律不报**（`$…$`、`\\(…\\)`、`\\begin{equation}` 内的空格/标点/连字符）。",
    "**忽略异型空格与间距**（`~`、窄空格、全角空格、多余空格）——不报、不改。",
)

_IRON_EN = (
    "**Never rewrite the manuscript**; do not polish, add or delete content — report only.",
    "**No tables**; reply with plain text and code blocks only.",
    "Always locate by **Exact Match**: quote the original fragment exactly — no rewording, no omissions, no ellipses.",
    "**Never report anything inside math** (`$…$`, `\\(…\\)`, `\\begin{equation}`): spaces, punctuation, hyphens.",
    "**Ignore non-ASCII spacing** (`~`, thin spaces, full-width spaces, extra spaces) — do not report, do not change.",
)

#: 正反例：bib 2 正 2 反 + 全文 1 正 1 反（C6）。全部合成脱敏（`San_Zhang`/`Doe`）。
_EXAMPLES_ZH = (
    "✅ bib：`@article{San_Zhang2020, ...}` 字段齐全 → 只确认，不改。",
    "✅ bib：`author = {Doe, J.}` 已倒装 → 只确认，不改。",
    "❌ bib：把 `Doe, J.` 改成 `John Doe` → 体例改写，越界，不报。",
    "❌ bib：给 `title` 加花括号保护大小写 → 越界，不报。",
    "✅ 全文：`见 \\cite{San_Zhang2020}` 与 bib 键不一致 → 只报「键不存在」并给 Exact Match 片段。",
    "❌ 全文：把 `$x=1$` 报成「空格不规范」→ 数学区不报。",
)

_EXAMPLES_EN = (
    "✅ bib: `@article{San_Zhang2020, ...}` with all required fields → confirm only, do not change.",
    "✅ bib: `author = {Doe, J.}` already inverted → confirm only, do not change.",
    "❌ bib: rewriting `Doe, J.` as `John Doe` → style rewrite, out of scope, do not report.",
    "❌ bib: adding braces to `title` to protect capitalisation → out of scope, do not report.",
    "✅ full text: `see \\cite{San_Zhang2020}` with a key absent from the bib → report \"key not found\" only, with an Exact Match fragment.",
    "❌ full text: reporting `$x=1$` as \"bad spacing\" → math, never report.",
)


def _ruleset_id(ruleset_id: str) -> str:
    if ruleset_id:
        return str(ruleset_id)
    from . import ruleset  # 延迟导入：本模块不 import 规则表，只借它的 id
    return ruleset.RULESET_ID


def _digest(rule_digest: str) -> str:
    if rule_digest:
        return str(rule_digest)
    from . import ruleset
    return ruleset.rules_digest()


def render_card(kind: str, lang: str, task_id: str, rule_digest: str = "",
                *, coop: bool = True, ruleset_id: str = "") -> str:
    """渲染任务卡（markdown 文本，≤60 行；zh/en 两套）。

    `kind` 只影响显示（`BIB`/`FULL`，与 `task_id()` 的前缀同源）；`coop` 控制要不要
    写明「第 10 条只覆盖 BibTeX 部分、附一 JSON 单列」——非协同任务书里没有附一，
    提它就是假的。`rule_digest`/`ruleset_id` 留空时现算（测试可注入固定值）。
    """
    en = str(lang).lower() == "en"
    prefix = str(kind or "BIB").upper()
    rs = _ruleset_id(ruleset_id)
    dg = _digest(rule_digest)
    from . import ruleset
    no_report = ruleset.no_report_lines("en" if en else "zh")
    n_rules = len(no_report)

    L: list[str] = []
    if en:
        L += [
            "## Task card (read this page first; it has the same force as the spec below)",
            "",
            f"- {TASK_ID_LABEL_EN} `{task_id}` | **Taskbook version**: v{TASKBOOK_VERSION} | "
            f"**Ruleset**: `{rs}` (digest `{dg}`)",
            "- **Role**: bibliography / full-text verification assistant — **report** problems only; "
            "never rewrite the manuscript or the references.",
            "- **Deliverable summary**: a complete, compilable `bibtex` block for `reference.bib`; "
            "per-item findings as the Appendix-1 JSON (`id` / `verdict` / `span` / `field` / `value`).",
        ]
        if coop:
            L.append(
                "  - Item 10 (\"output only the bibtex block\") covers the **BibTeX part only**; "
                "send the Appendix-1 JSON in a **separate** code block — Item 10 does not apply to it.")
        L += [
            f"- **Iron rules ({len(_IRON_EN)}, any violation voids that suggestion)**:",
            *[f"  {i}. {t}" for i, t in enumerate(_IRON_EN, 1)],
            "",
            f"- **Do-not-report list ({n_rules} rules; generated from `proofcheck.RULES`; "
            "format `id · what · default action`)**:",
            *[f"  - {ln}" for ln in no_report],
            "",
            "- **Examples (all synthetic and anonymised; no real manuscript content)**:",
            *[f"  - {ln}" for ln in _EXAMPLES_EN],
        ]
    else:
        L += [
            "## 任务卡（先读这一页；与下面的规范同等效力）",
            "",
            f"- {TASK_ID_LABEL_ZH}`{task_id}`｜**任务书版本**：v{TASKBOOK_VERSION}｜"
            f"**规则集**：`{rs}`（摘要 `{dg}`）",
            "- **角色**：文献核实/全文核查助手——只**报告**问题，**不改写**正文或文献条目。",
            "- **交付摘要**：`reference.bib` 给完整可编译的 bibtex 代码块；"
            "逐条问题按附一 JSON 回复（`id`/`verdict`/`span`/`field`/`value`）。",
        ]
        if coop:
            L.append(
                "  - 第 10 条「只输出那个 bibtex 代码块」**只覆盖 BibTeX 部分**；"
                "附一 JSON 另起代码块**单列**，不受第 10 条限制。")
        L += [
            f"- **铁律（{len(_IRON_ZH)} 条，违反任一条该条建议作废）**：",
            *[f"  {i}. {t}" for i, t in enumerate(_IRON_ZH, 1)],
            "",
            f"- **免报清单（{n_rules} 条，生成自 `proofcheck.RULES`；格式 `id · 检查什么 · 默认动作`）**：",
            *[f"  - {ln}" for ln in no_report],
            "",
            "- **正反例（全部为合成脱敏样例，不含真实稿件内容）**：",
            *[f"  - {ln}" for ln in _EXAMPLES_ZH],
        ]
    return "\n".join(L)
