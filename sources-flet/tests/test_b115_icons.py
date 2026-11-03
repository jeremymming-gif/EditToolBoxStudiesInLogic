"""批 115 · P3-B 图标静态契约（不启 GUI、不跑打包、不联网）。

口径（DECISIONS D1-115-03）：打包 / 窗口 / 托盘图标统一由
`packaging/assets/glass-slate-ink-1024.png` 派生；`logo.icns` / `logo.ico`
**文件名不变**（`.github/workflows/*.yml` 的 `flet pack --icon` 已按名引用），只换内容。
本文件只断言「文件在、头对、尺寸对、引用不死」，渲染效果由 evidence/b115-pack/preview.png 人眼核。
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
ASSETS = APP.parent / "packaging" / "assets"
ROOT = APP.parents[1]

ICO_SIZES = {(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}


def _icns_chunks(data: bytes) -> dict[bytes, int]:
    """按 ICNS 规范切块（length 字段含 8 字节头），顺带验证头长度与切片闭合。"""
    assert data[:4] == b"icns", "icns 魔数不对（flet pack --icon 会直接失败）"
    assert int.from_bytes(data[4:8], "big") == len(data), "icns 头声明长度与文件不符"
    chunks: dict[bytes, int] = {}
    offset = 8
    while offset < len(data):
        kind = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "big")
        assert length >= 8 and offset + length <= len(data), f"icns 块长度越界: {kind!r}"
        chunks[kind] = length
        offset += length
    assert offset == len(data), "icns 块长度未闭合"
    return chunks



def test_pack_icon_files_exist_and_are_nonempty():
    for name in ("logo.icns", "logo.ico",
                 "glass-slate-ink-64.png", "glass-slate-ink-256.png"):
        path = ASSETS / name
        assert path.is_file(), f"缺打包资产: {name}"
        assert path.stat().st_size > 0, f"空资产: {name}"


def test_icns_has_valid_header_and_multi_size_payload():
    chunks = _icns_chunks((ASSETS / "logo.icns").read_bytes())
    # 16/32 在 iconutil 现输出里是 ic04/ic05（ARGB），PNG 版是 icp4/icp5 —— 两代编码都接受。
    assert {b"ic04", b"icp4"} & chunks.keys(), "icns 缺 16px 档"
    assert {b"ic05", b"icp5"} & chunks.keys(), "icns 缺 32px 档"
    # 128 / 256 / 512 / 1024（512@2x）四档必须齐全，Dock 与 Retina 都吃这几档。
    assert {b"ic07", b"ic08", b"ic09", b"ic10"} <= chunks.keys(), sorted(chunks)
    # @2x 档：16@2x(ic11) / 32@2x(ic12) / 128@2x(ic13) / 256@2x(ic14) 至少一半以上。
    retina = {b"ic11", b"ic12", b"ic13", b"ic14"} & chunks.keys()
    assert len(retina) >= 2, f"icns Retina 档不足: {sorted(chunks)}"


def test_ico_has_valid_header_and_six_sizes():
    data = (ASSETS / "logo.ico").read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00", "ico 头不对（Windows 窗口图标不可用）"
    with Image.open(ASSETS / "logo.ico") as image:
        assert ICO_SIZES <= set(image.ico.sizes()), f"ico 尺寸不足: {sorted(image.ico.sizes())}"


def test_derived_pngs_match_slate_master():
    with Image.open(ASSETS / "glass-slate-ink-1024.png") as master:
        assert master.size == (1024, 1024) and master.mode == "RGBA"
    for name, side in (("glass-slate-ink-64.png", 64), ("glass-slate-ink-256.png", 256)):
        with Image.open(ASSETS / name) as image:
            assert image.size == (side, side), name
            assert image.mode == "RGBA", name
            assert image.getbbox() is not None, f"{name} 全透明，等于没图标"


def test_brand_prefers_glass_assets_and_keeps_legacy_fallback():
    from ui import brand

    assert brand.asset(brand.TRAY_PNG).endswith("glass-slate-ink-64.png")
    assert brand.asset(brand.WINDOW_PNG).endswith("glass-slate-ink-256.png")
    # 兼容：旧常量仍可解析（.ico 内容已换玻璃 slate，文件名不变）
    assert brand.asset(brand.WINDOW_ICON).endswith("logo.ico")
    assert brand.asset(brand.SIDEBAR_PNG).endswith("logo-256.png")
    page = SimpleNamespace(window=SimpleNamespace())
    assert brand.apply_window_icon(page).endswith("logo.ico")
    image = brand.tray_image()
    assert image is not None and image.size == (64, 64)


def test_brand_asset_constants_all_resolve_no_dead_refs():
    from ui import brand

    names = [brand.SIDEBAR_PNG, brand.WINDOW_ICON, brand.WINDOW_PNG, brand.TRAY_PNG,
             brand.ALEPH_LIGHT, brand.ALEPH_DARK, brand.ALEPH_DARK_FALLBACK]
    missing = [name for name in names if not (ASSETS / name).is_file()]
    assert missing == [], f"brand.py 死引用: {missing}"


def test_workflow_flet_pack_icon_paths_resolve():
    refs: list[tuple[str, str]] = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"--icon\s+(\S+)", text):
            ref = match.group(1).replace("\\", "/")
            refs.append((path.name, ref.rsplit("assets/", 1)[-1] if "assets/" in ref else ref))
    assert refs, "workflow 里应至少有 flet pack --icon 引用"
    missing = [(workflow, name) for workflow, name in refs if not (ASSETS / name).is_file()]
    assert missing == [], f"workflow 图标死引用: {missing}"


def test_packaging_readme_documents_new_icon_pipeline():
    text = (APP.parent / "packaging" / "README.md").read_text(encoding="utf-8")
    assert "make_pack_icons.py" in text
    assert "glass-slate-ink-1024.png" in text
    assert "logo.icns" in text and "logo.ico" in text
