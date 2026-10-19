"""Windows 外壳的本地配置（不含界面代码）。

放哪：Windows 用 `%APPDATA%\\EditToolBox\\settings.json`；macOS（开发时）用
`~/Library/Application Support/EditToolBox/`；其它平台 `~/.config/EditToolBox/`。

里面有 API key，所以位置与权限都要收紧 —— 但两个平台的"收紧"**不是一回事**：

  · POSIX（mac 开发时 / Linux）：`os.chmod(path, 0o600)` 真的生效，只有本人可读写；
  · Windows：**没有 POSIX 权限位** —— `os.chmod(0o600)` 在那里只退化成「清只读位」，
    `st_mode & 0o777` 恒为 0o666。真正的保护来自 `%APPDATA%` 的 ACL（默认只给本用户
    + SYSTEM + Administrators，别的标准用户读不到），也就是 Windows 存放密钥的通行做法。
    所以这里**刻意不去改 ACL**：改错了会让配置文件自己读不出来 —— 正是第 62 条那次
    「用户以为存上了、其实没有」的创伤；位置本身已经够了。

⚠️ key **只落本地**，不出现在命令行参数里（`engine.env_for_run()` 用环境变量传给引擎），
界面显示时一律打码。
"""

from __future__ import annotations

import json
import os
import stat
import sys

APP_DIR_NAME = "EditToolBox"

#: 与 mac 版一致的 AI 字段（`Engine/slcn/llm.py` 认这些环境变量）
API_KEYS = ("base", "key", "model", "protocol", "thinking",
            "maxTokens", "webSearch", "searchTool", "responsesPath")

DEFAULTS: dict = {
    # webSearch 与 searchRefs 是**布尔**（其余是字符串）——两个检索开关独立：
    # webSearch = 模型内置检索工具；searchRefs = 工具自己用 DeepSeek Search API 检索证据。
    # budgetTokens / batchBudgetTokens 是**非负整数**（0 = 不限），只约束直连 API 通道
    # （第五十五批；两段式外部 chat 流程零费用，不传这两个参数）。
    "api": {**{k: "" for k in API_KEYS}, "webSearch": False, "searchRefs": False,
            "budgetTokens": 30000, "batchBudgetTokens": 1000000},
    "options": {
        "convertCompile": True,      # 初排转换默认编译 PDF（编辑最常要的产物）
        "convertClean": False,       # 默认保留中间文件，便于排查
        "convertVerifyRefs": True,   # 默认出一份《文献核实任务书》
        "convertSourcePdf": False,
        "mergeEditorsPage": True,
        "mergeBackCover": False,
        "mergeNestOutlines": True,
    },
    "engineDir": "",
    # U4（2026-10-17 第十五批）日志两层：True = 显示「原始」层，False = 摘要（默认折叠原始）。
    # 放在顶层而不是 options：它是**界面偏好**，不是任务选项。
    "logShowRaw": False,
    # ── 第 132 条：引用库（核实成本闸门 / 先查库 / 笔记仓路径）──
    # 默认值刻意是「**一个参数都不加**」：
    #   · verifyMax=None         → 不传 --verify-max（引擎默认 120 条；0 = 不限）
    #   · verifyPlan=False       → 不传 --verify-plan（照常核实）
    #   · verifyAllRefs=False    → 不传 --verify-all-refs（受上限约束）
    #   · lookupEnabled=True     → 不传 --no-bibdb（引擎默认就是「库存在就查」）
    # 于是没碰过这些设置的用户，convert 命令行与接入引用库之前**逐字节相同**。
    "bibdb": {
        "verifyMax": None,
        "verifyPlan": False,
        "verifyAllRefs": False,
        "lookupEnabled": True,
    },
    # 手动指定的私有笔记仓（引用库在 <它>/bib_db/）；空 = 自动探测（第 132 条）。
    "notesRepoPath": "",
}

#: 引擎侧的默认核实上限（`Engine/slcn/refmark.py` 的 DEFAULT_VERIFY_MAX，只有一处）。
#: 这里只是给界面显示「留空 = 引擎默认 120 条」，**不参与判定**。
DEFAULT_VERIFY_MAX = 120

#: 引擎 `convert.py` 的 `--ai-budget-tokens` / `--ai-batch-budget-tokens` **argparse 默认值**
#: （第五十四批；两处口径必须一致）。设置页默认显示这两个数；传 CLI 的规则是
#: 「值 != 这里的默认才传」——于是没动过预算的用户，命令行与批54 逐字节相同。
AI_BUDGET_DEFAULT = 30000
AI_BATCH_BUDGET_DEFAULT = 1000000


def config_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME)


def config_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def _merge_defaults(raw: dict) -> dict:
    """与默认值合并（旧版本留下的配置文件缺字段时不许炸）。"""
    out = json.loads(json.dumps(DEFAULTS))          # 深拷贝
    for section in ("api", "options"):
        got = raw.get(section)
        if isinstance(got, dict):
            out[section].update({k: v for k, v in got.items() if k in out[section]})
    # 第五十五批：AI 预算只收**非负整数**（0 = 不限）。配置文件是编辑能手改的文本，
    # 也可能是旧版本留下的垃圾 —— 非法值回默认，绝不让它进 argv（引擎会拒绝）。
    for key in ("budgetTokens", "batchBudgetTokens"):
        value = out["api"].get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            out["api"][key] = DEFAULTS["api"][key]
    if isinstance(raw.get("engineDir"), str):
        out["engineDir"] = raw["engineDir"]
    # 顶层布尔偏好（U4：日志「摘要/原始」选择）—— 从前面板外键会在这里被丢掉，
    # 于是「偏好落盘了、下次启动却不生效」。逐键白名单，避免把任意垃圾键带进来。
    for key in ("logShowRaw",):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    # 第 132 条：引用库设置。**逐键校验**，不信配置文件里的任意垃圾：
    # verifyMax 只收 ≥0 的 int（None = 没设过）；三个开关只收 bool。
    got_bibdb = raw.get("bibdb")
    if isinstance(got_bibdb, dict):
        for key in DEFAULTS["bibdb"]:
            if key not in got_bibdb:
                continue
            value = got_bibdb[key]
            if key == "verifyMax":
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    out["bibdb"][key] = value
                continue
            if isinstance(value, bool):
                out["bibdb"][key] = value
    if isinstance(raw.get("notesRepoPath"), str):
        out["notesRepoPath"] = raw["notesRepoPath"]
    return out


def load() -> dict:
    """读配置；文件不存在/损坏一律回默认值（**不抛**，界面不该因为配置文件打不开就起不来）。"""
    path = config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            return _merge_defaults(raw)
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001  坏 JSON / 没权限：用默认值继续
        pass
    return _merge_defaults({})


def save(cfg: dict) -> str:
    """写配置（POSIX 上 0600；Windows 见模块抬头 —— 那里靠 `%APPDATA%` 的 ACL）。

    返回写入路径；失败时抛异常，由界面提示。
    """
    path = config_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)                            # 原子替换：写坏了一半也不会毁掉旧配置
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def mask_key(key: str) -> str:
    """界面显示用：只留前 4 位。key 为空时返回空串。"""
    key = (key or "").strip()
    if not key:
        return ""
    return key[:4] + "…" if len(key) > 4 else "…"


# ── 第 132 条：引用库设置 → 外壳/引擎（纯函数，桥接测试直接打这些）──────────
# 为什么要有这一层：界面（引用库设置页）只负责把文本写进 cfg，而「哪些值真的进
# convert 的 argv」必须**只有一处判据** —— 否则设置页说一套、转换页传另一套。
# 与 mac 侧 `BibDbSettings`（UserDefaults → ConvertFeature.arguments）同一职责。

def parse_verify_max(text: str) -> tuple[int | None, str | None]:
    """解析「核实上限」输入框：返回 `(要保存的值, 错误提示)`。

    · 留空 → `(None, None)`：没设过 → **不传** `--verify-max`（引擎默认 120 条）；
    · 合法非负整数 → `(n, None)`（0 = 不限，会被真的传下去）；
    · 非法 → `(None, 错误话术)`：**不落盘**，界面红字提示留在输入框里让编辑改。
    """
    value = (text or "").strip()
    if not value:
        return None, None
    try:
        number = int(value)
    except ValueError:
        return None, f"上限必须是整数（0 = 不限；留空 = 引擎默认 {DEFAULT_VERIFY_MAX} 条）"
    if number < 0:
        return None, "上限不能是负数（要「不限」请填 0）"
    return number, None


def verify_max_text(cfg: dict) -> str:
    """设置页输入框的初始文本：设过就显示数字，没设过显示空（占位提示引擎默认）。"""
    value = (cfg or {}).get("bibdb", {}).get("verifyMax")
    return "" if value is None else str(value)


def verify_max_summary(cfg: dict) -> str:
    """一行摘要（转换页/设置页的「当前生效值」共用）。"""
    value = (cfg or {}).get("bibdb", {}).get("verifyMax")
    if value is None:
        return f"引擎默认 {DEFAULT_VERIFY_MAX} 条（本次不传 --verify-max）"
    return "不限（--verify-max 0）" if value == 0 else f"{value} 条（--verify-max）"


def bibdb_convert_args(cfg: dict) -> dict:
    """引用库设置 → `engine.convert_call()` 的额外参数（**默认返回空 dict**）。

    只在 `verifyMax` 真的设过 / 开关真的偏离默认时才给键，这样「没碰过设置」的
    用户传进 `convert_call` 的 kwargs 为空 → 命令行逐字节不变（第 132 条的红线）。
    `lookupEnabled=True`（默认）**不产生任何参数**：查库是引擎默认行为，只有显式关
    才传 `--no-bibdb`。
    """
    opts = (cfg or {}).get("bibdb") or {}
    args: dict = {}
    max_refs = opts.get("verifyMax")
    if isinstance(max_refs, int) and not isinstance(max_refs, bool) and max_refs >= 0:
        args["verify_max"] = max_refs
    if opts.get("verifyPlan") is True:
        args["verify_plan"] = True
    if opts.get("verifyAllRefs") is True:
        args["verify_all_refs"] = True
    if opts.get("lookupEnabled") is False:
        args["no_bibdb"] = True
    return args


# ── 第五十五批：AI 直连 API 的 token 预算闸（纯函数，桥接测试直接打这些）──────
# 为什么单独一层：设置页只负责把文本写进 cfg，而「哪些值真的进 convert 的 argv」
# 必须只有一处判据 —— 与 mac 侧同一规则（默认零变化 + 0 = 不限）。

def parse_budget_tokens(text: str, default: int) -> tuple[int | None, str | None]:
    """解析预算输入框：返回 `(要保存的值, 错误提示)`。

    · 留空 → `(default, None)`：用引擎默认（不传 CLI 参数）；
    · 合法非负整数 → `(n, None)`（0 = 不限，会**真的**传下去）；
    · 非法（负数/非整数）→ `(None, 错误话术)`：**不落盘**，界面红字提示。
    """
    value = (text or "").strip()
    if not value:
        return default, None
    try:
        number = int(value)
    except ValueError:
        return None, f"预算必须是整数（默认 {default}；0 = 不限）"
    if number < 0:
        return None, "预算不能是负数（要「不限」请填 0）"
    return number, None


def ai_configured(cfg: dict) -> bool:
    """直连 API 通道是否可用：**地址 + 模型名**（密钥可空——本机/自建端点不要密钥）。

    口径与引擎 `slcn/llm.py` 的 `LLMConfig.ready`（`base and model`）一致：
    界面说「预算生效」时引擎那边必须真的会走直连调用。
    """
    api = (cfg or {}).get("api") or {}
    return bool(str(api.get("base", "")).strip() and str(api.get("model", "")).strip())


def ai_budget_convert_args(cfg: dict, *, coop: bool = False) -> dict:
    """预算设置 → `engine.convert_call()` 的 kwargs（**默认返回空 dict**）。

    规则（两壳一致，见 `drafts/batch55-gui-plan.md` §3.2）：
      · 两段式协同（`coop=True`）= 外部 chat 通道，软件零费用 → 一个参数都不传；
      · 直连 API 没配置（地址/模型缺一）→ 不传（引擎不会发起调用，传了只是噪声）；
      · 值 == 引擎默认（30000 / 1000000）→ 不传（命令行与批54 逐字节相同）；
      · 值 != 默认才传；**0 要真的传**（0 = 不限，不能当默认值省掉）。
    """
    if coop:
        return {}
    if not ai_configured(cfg):
        return {}
    api = (cfg or {}).get("api") or {}
    args: dict = {}
    for key, default, name in (("budgetTokens", AI_BUDGET_DEFAULT, "ai_budget_tokens"),
                               ("batchBudgetTokens", AI_BATCH_BUDGET_DEFAULT,
                                "ai_batch_budget_tokens")):
        value = api.get(key)
        if (isinstance(value, int) and not isinstance(value, bool)
                and value >= 0 and value != default):
            args[name] = value
    return args
