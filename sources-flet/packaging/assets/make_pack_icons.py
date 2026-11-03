"""P3 打包图标生成器（批 115 · P3-B）· 一次从玻璃 slate 母版派生，产物入库。

口径（DECISIONS D1-115-03）：`.icns` / `.ico` / 托盘 PNG 一律取
`glass-slate-ink-1024.png`（深色 ℵ + 浅灰玻璃板，与 dock-compare mock A 卡一致）；
`glass-slate-white-1024.png`（白 ℵ）是备选，切换只改 `MASTER` 一行。

为什么文件名仍是 `logo.icns` / `logo.ico`：`.github/workflows/build-flet.yml`、
`release.yml` 的 `flet pack --icon` 已按这两个名字引用；改名会动发版链，本批不碰。
旧 `make_logo.py` 只保留矢量母版 `logo-1024/256.png`，不再写 ico/icns（防止把
本脚本的产物覆盖回旧品牌）。

产物：
- `logo.icns`：iconutil 打包的 iconset（16 … 512@2x，即 16–1024 共 10 档）；
- `logo.ico`：Pillow 写 16/32/48/64/128/256 六档（Windows 窗口/任务栏）；
- `glass-slate-ink-64.png` / `glass-slate-ink-256.png`：托盘 / 窗口 PNG 派生；
- `--preview PATH`：16/32/64/128/256 缩略合成（带标注，落证据目录，不进仓）。

重跑（幂等，仅需 Pillow；mac 上 iconutil 由系统提供）：
    python sources-flet/packaging/assets/make_pack_icons.py
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
MASTER = "glass-slate-ink-1024.png"
ICNS_NAME = "logo.icns"
ICO_NAME = "logo.ico"
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
#: iconutil 认的 iconset 文件名 → 像素边长；@2x 与 1x 同像素时复用同一档渲染。
ICONSET = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]
DERIVED = [("glass-slate-ink-64.png", 64), ("glass-slate-ink-256.png", 256)]


def _load() -> Image.Image:
    path = HERE / MASTER
    image = Image.open(path).convert("RGBA")
    if image.size != (1024, 1024):
        raise SystemExit(f"{MASTER} 必须是 1024×1024，实际 {image.size}")
    return image


def _resize(master: Image.Image, size: int) -> Image.Image:
    return master.resize((size, size), Image.LANCZOS)


def build_icns(master: Image.Image) -> Path:
    """先落 iconset，再交给系统 iconutil 打 .icns（Pillow 的 ICNS 写不出 @2x 全套）。"""
    out = HERE / ICNS_NAME
    with tempfile.TemporaryDirectory(prefix="b115-iconset-") as tmp:
        iconset = Path(tmp) / "logo.iconset"
        iconset.mkdir()
        for name, size in ICONSET:
            _resize(master, size).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                       check=True)
    return out


def build_ico(master: Image.Image) -> Path:
    out = HERE / ICO_NAME
    # 传 1024 母版 + sizes：Pillow 逐档 LANCZOS 缩放写入，ico 头记录六档。
    master.save(out, format="ICO", sizes=ICO_SIZES)
    return out


def build_derived(master: Image.Image) -> list[Path]:
    made = []
    for name, size in DERIVED:
        path = HERE / name
        _resize(master, size).save(path, format="PNG", optimize=True)
        made.append(path)
    return made


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1：无 size 参数，退回内置位图字体
        return ImageFont.load_default()


def build_preview(master: Image.Image, dest: Path) -> Path:
    """缩略合成：自然尺寸一排，逐档标注（16/32/64/128/256），证明小尺寸可读。"""
    sizes = [16, 32, 64, 128, 256]
    gap, margin, label_h = 24, 24, 30
    width = margin * 2 + sum(sizes) + gap * (len(sizes) - 1)
    height = margin * 2 + max(sizes) + label_h
    canvas = Image.new("RGBA", (width, height), (243, 244, 246, 255))
    draw = ImageDraw.Draw(canvas)
    font = _font(18)
    x = margin
    top = margin + max(sizes)  # 底对齐
    for size in sizes:
        thumb = _resize(master, size)
        canvas.alpha_composite(thumb, (x, top - size))
        label = str(size)
        try:
            box = draw.textbbox((0, 0), label, font=font)
            text_w = box[2] - box[0]
        except Exception:  # noqa: BLE001 - 极老 Pillow 无 textbbox
            text_w = 6 * len(label)
        draw.text((x + (size - text_w) / 2, top + 6), label, fill=(31, 41, 55, 255),
                  font=font)
        x += size + gap
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(dest)
    return dest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    master = _load()
    preview = None
    if "--preview" in argv:
        preview = Path(argv[argv.index("--preview") + 1]).resolve()
    made = [build_icns(master), build_ico(master), *build_derived(master)]
    if preview is not None:
        made.append(build_preview(master, preview))
    for path in made:
        print(f"{path}  sha256={sha256(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
