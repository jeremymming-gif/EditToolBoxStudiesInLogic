"""批 113 E：TextField 弃用参数 border_color / focused_border_color → border 状态映射。

Flet 1.0 起 `border_color` / `focused_border_color` 已标记 deprecated（delete_version
1.3.0），新写法是 `border=ControlStateValue[InputBorder]`。本测试：
① AST 扫描 app 全部 .py，断言再无这两个关键字参数；
② 断言迁移落到 `OutlineInputBorder(`，且不再出现 `UnderlineInputBorder(`；
   Flet 1.0.5 的 TextField 默认边框就是 OutlineInputBorder（最小 app 实测，证据
   shots/tdtest-border-types.png），换成 Underline 会把方框变成下划线、破坏外观等价；
③ 挑两个真实构造点（base_page._meta_field、bib_ops._input_card），用 Fake tokens
   执行其 `border=` 表达式，核对 DEFAULT 用 palette.border、FOCUSED 用 palette.accent。
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import flet as ft

APP = Path(__file__).resolve().parents[1] / "app"
DEPRECATED = {"border_color", "focused_border_color"}


def _py_files() -> list[Path]:
    return sorted(path for path in APP.rglob("*.py") if "__pycache__" not in path.parts)


def _border_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "TextField" and any(kw.arg == "border" for kw in node.keywords):
            calls.append(node)
    return calls


def test_no_deprecated_border_kwargs_anywhere():
    offenders = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in DEPRECATED:
                        offenders.append(f"{path.relative_to(APP)}:{node.lineno}:{kw.arg}=")
    assert offenders == [], "仍有弃用参数：" + ", ".join(offenders)


def test_border_dict_migration_present():
    hits = sum(path.read_text(encoding="utf-8").count("OutlineInputBorder(")
               for path in _py_files())
    assert hits >= 20, f"OutlineInputBorder 只出现 {hits} 次，迁移不完整"
    built = sum(len(_border_calls(path)) for path in _py_files())
    assert built >= 10, f"带 border= 的 TextField 只有 {built} 个"
    underline = sum(path.read_text(encoding="utf-8").count("UnderlineInputBorder(")
                    for path in _py_files())
    assert underline == 0, "UnderlineInputBorder 会把方框边框变成下划线，破坏外观等价"


def _eval_border(path: Path, node: ast.Call) -> dict:
    """用 Fake tokens 执行该 TextField 的 border= 表达式（只依赖 ft 与 t.palette）。"""
    border = next(kw.value for kw in node.keywords if kw.arg == "border")
    source = ast.get_source_segment(path.read_text(encoding="utf-8"), border)
    assert source and source.lstrip().startswith("{"), source
    fake_t = SimpleNamespace(palette=SimpleNamespace(border="#123456", accent="#abcdef"))
    return eval(source, {"ft": ft, "t": fake_t, "self": SimpleNamespace(t=fake_t)})  # noqa: S307


def _check_site(rel: str) -> None:
    path = APP / rel
    calls = _border_calls(path)
    assert calls, f"{rel} 没有带 border= 的 TextField"
    node = calls[0]
    border = _eval_border(path, node)
    assert isinstance(border, dict), f"{rel}:{node.lineno} border 不是状态映射"
    assert set(border) == {ft.ControlState.DEFAULT, ft.ControlState.FOCUSED}
    for state in (ft.ControlState.DEFAULT, ft.ControlState.FOCUSED):
        edge = border[state]
        assert isinstance(edge, ft.OutlineInputBorder), f"{rel}:{state} 不是 OutlineInputBorder"
        assert isinstance(edge.side, ft.BorderSide)
        # 外观等价：默认态沿用 Material outline 1px；聚焦态沿用原主题的 2px 加粗。
        expected = 1 if state == ft.ControlState.DEFAULT else 2
        assert edge.side.width == expected, f"{rel}:{state} 宽度 {edge.side.width} != {expected}"
    assert border[ft.ControlState.DEFAULT].side.color == "#123456"  # palette.border
    assert border[ft.ControlState.FOCUSED].side.color == "#abcdef"  # palette.accent


def test_site_border_colors_use_palette():
    _check_site("ui/base_page.py")
    _check_site("ui/bib_ops.py")
