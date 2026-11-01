"""批 112：Flet 控件构造契约测试（纯反射，mac/Windows 均可跑）。

背景（GUI-04 族）：flet 1.0.5 把若干控件的回调参数改名（如 `Dropdown.on_change` →
`on_select`），而旧代码在渲染到该控件时才炸。这里用 AST 把 `sources-flet/app/**/*.py`
里所有 `ft.<控件>(...)` 的**关键字实参与 flet 1.0.5 签名**对照，静态拦住这类问题。

只检查签名显式（无 `**kwargs`）的 flet 公开类：Border/BorderSide/Theme 等接受
VAR_KEYWORD 的类跳过，避免误报。
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import flet as ft
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "sources-flet" / "app"


def _flet_control_classes() -> dict[str, type]:
    out = {}
    for name in dir(ft):
        obj = getattr(ft, name)
        if isinstance(obj, type) and getattr(obj, "__module__", "").startswith("flet"):
            out[name] = obj
    return out


def _signatures() -> dict[str, inspect.Signature]:
    sigs = {}
    for name, cls in _flet_control_classes().items():
        try:
            sig = inspect.signature(cls.__init__)
        except (ValueError, TypeError):  # pragma: no cover - C 扩展等
            continue
        params = sig.parameters
        if any(p.kind is p.VAR_KEYWORD for p in params.values()):
            continue  # 接受任意 kwargs 的类不参与检查
        sigs[name] = sig
    return sigs


def _iter_calls():
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id != "ft":
                continue
            yield path, node, func.attr


def test_flet_control_keywords_match_flet_1_0_5_signature():
    sigs = _signatures()
    assert sigs, "未解析到任何 flet 控件签名"
    problems: list[str] = []
    for path, call, attr in _iter_calls():
        sig = sigs.get(attr)
        if sig is None:
            continue
        params = sig.parameters
        allowed = set(params) - {"self"}
        for kw in call.keywords:
            if kw.arg is None:  # **mapping，静态无法判断
                continue
            if kw.arg not in allowed:
                rel = path.relative_to(REPO_ROOT)
                problems.append(f"{rel}:{call.lineno} ft.{attr}(..., {kw.arg}=...) 不是合法参数")
    assert not problems, "flet 控件构造参数与 1.0.5 签名不符：\n" + "\n".join(problems)


def test_flet_control_positional_args_within_limit():
    sigs = _signatures()
    problems: list[str] = []
    for path, call, attr in _iter_calls():
        sig = sigs.get(attr)
        if sig is None:
            continue
        max_pos = sum(
            1 for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        positionals = [a for a in call.args if not isinstance(a, ast.Starred)]
        if len(positionals) > max_pos:
            problems.append(
                f"{path.relative_to(REPO_ROOT)}:{call.lineno} ft.{attr} 位置参数 {len(positionals)} > {max_pos}"
            )
    assert not problems, "flet 控件位置参数过多：\n" + "\n".join(problems)


@pytest.mark.parametrize("forbidden", ["on_change"])
def test_dropdown_has_no_on_change_regression(forbidden: str):
    """显式回归：Dropdown.on_change 是 flet 1.0.5 已改名的参数（应使用 on_select）。"""
    sigs = _signatures()
    assert forbidden not in sigs["Dropdown"].parameters
