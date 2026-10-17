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
    "api": {**{k: "" for k in API_KEYS}, "webSearch": False, "searchRefs": False},
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
}


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
    if isinstance(raw.get("engineDir"), str):
        out["engineDir"] = raw["engineDir"]
    # 顶层布尔偏好（U4：日志「摘要/原始」选择）—— 从前面板外键会在这里被丢掉，
    # 于是「偏好落盘了、下次启动却不生效」。逐键白名单，避免把任意垃圾键带进来。
    for key in ("logShowRaw",):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
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
