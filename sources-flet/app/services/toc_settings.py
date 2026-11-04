"""目录设置.json 的读写与校验（外壳侧，不复制排版逻辑）。

写入前备份原文件；原文件不是合法 JSON 时拒绝覆盖，避免把人工栏目表写坏。
字段与引擎 `Engine/slcn/toc.py` 的 TocSettings 对齐，缺字段按默认值补齐。
"""
from __future__ import annotations

import json
import os
import time

FILE_NAME = "目录设置.json"
COLUMN_KINDS = ("", "专栏", "专辑")
KIND_LABELS = {"": "本期无栏目", "专栏": "专栏", "专辑": "专辑"}


def defaults() -> dict:
    return {
        "columnKind": "",
        "columnName": "",
        "columnNameEN": "",
        "columnHost": "",
        "columnArticles": [],
        "templateFolder": "",
        "titleWrapLimit": 20.0,
        "titleWrapLimitEN": 30.0,
    }


def _float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def normalize(raw) -> dict:
    """把任意读入值整理成可写回引擎的八个字段。"""
    data = defaults()
    if not isinstance(raw, dict):
        return data
    data["columnKind"] = str(raw.get("columnKind") or "").strip()
    for key in ("columnName", "columnNameEN", "columnHost", "templateFolder"):
        data[key] = str(raw.get(key) or "").strip()
    articles = raw.get("columnArticles")
    if isinstance(articles, list):
        data["columnArticles"] = [str(x).strip() for x in articles if str(x).strip()]
    data["titleWrapLimit"] = _float(raw.get("titleWrapLimit"), 20.0)
    data["titleWrapLimitEN"] = _float(raw.get("titleWrapLimitEN"), 30.0)
    return data


def path_for(folder: str) -> str:
    return os.path.join(str(folder or ""), FILE_NAME)


def read(path: str) -> tuple[dict, str]:
    """读设置文件：缺失 → 默认值；非法 JSON → 默认值 + 错误说明（不改文件）。"""
    target = str(path or "")
    if not target or not os.path.isfile(target):
        return defaults(), ""
    try:
        with open(target, encoding="utf-8") as fh:
            raw = json.load(fh)
    except OSError as exc:
        return defaults(), f"读不到 {FILE_NAME}：{exc}"
    except ValueError as exc:
        return defaults(), f"{FILE_NAME} 不是合法 JSON：{exc}（未改动该文件）"
    return normalize(raw), ""


def backup(path: str) -> str:
    """把原文件复制成同目录的 .bak-时间戳 备份，返回备份路径。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = f"{path}.bak-{stamp}"
    index = 1
    while os.path.exists(target):
        target = f"{path}.bak-{stamp}-{index}"
        index += 1
    with open(path, "rb") as src, open(target, "wb") as dst:
        dst.write(src.read())
    return target


def save(path: str, data: dict) -> tuple[bool, str]:
    """写设置文件；返回 (成功, 路径或错误说明)。

    原文件已存在且不是合法 JSON 时拒绝覆盖（先让人修好或删掉旧文件）。
    """
    target = str(path or "")
    if not target:
        return False, "没有保存位置：先选报告或目录设置文件。"
    if os.path.isfile(target):
        try:
            with open(target, encoding="utf-8") as fh:
                json.load(fh)
        except ValueError as exc:
            return False, f"{FILE_NAME} 不是合法 JSON，未写入：{exc}"
        except OSError as exc:
            return False, f"读不到 {FILE_NAME}，未写入：{exc}"
        backup(target)
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    payload = normalize(data)
    tmp = f"{target}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, target)
    return True, target
