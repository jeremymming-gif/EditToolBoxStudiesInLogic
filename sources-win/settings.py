"""Windows 外壳的本地配置（不含界面代码）。

放哪：Windows 用 `%APPDATA%\\EditToolBox\\settings.json`；macOS（开发时）用
`~/Library/Application Support/EditToolBox/`；其它平台 `~/.config/EditToolBox/`。
**权限 0600**：里面有 API key —— 与 mac 那套（`~/Library/Application Support/
cn.edu.sysu.logic.toolbox/ai.json`）同样的口径。

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
    "api": {k: "" for k in API_KEYS},
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
    """写配置（0600）。返回写入路径；失败时抛异常，由界面提示。"""
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
