"""品牌母版生成器（B-109 W4）· 一次画好，产物入库，运行时不画图。

设计约束（证据 typography.md / logo-assets.md §3）：
- 方形 1024×1024，圆角底 #4F46E5（与 tokens.LIGHT.accent 同色）；
- 单色负形：白色书页 + 折角 + 三条目录线，无渐变、无文字、无阴影；
- 4× 超采样后 LANCZOS 降采样，保证 16px 图标边缘干净。

产物：`logo-1024.png`、`logo-256.png`、`logo.ico`（16–256 多尺寸，Windows 打包用）。
重跑：`py -3.14 sources-flet/packaging/assets/make_logo.py`（幂等，无外部依赖，仅 Pillow）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SIZE = 1024
SS = 4                      # 超采样倍数
ACCENT = (79, 70, 229)      # #4F46E5
WHITE = (255, 255, 255)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _s(value: float) -> int:
    return round(value * SS)


def render() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    # 底：圆角方，四周留 64 设计像素的透明边（Windows 任务栏里不顶格）
    draw.rounded_rectangle((_s(64), _s(64), _s(960), _s(960)), radius=_s(224), fill=ACCENT)
    # 书页：矩形去掉右上折角
    left, top, right, bottom, fold = 312, 236, 712, 788, 96
    draw.polygon(
        [(_s(left), _s(top)), (_s(right - fold), _s(top)), (_s(right), _s(top + fold)),
         (_s(right), _s(bottom)), (_s(left), _s(bottom))],
        fill=WHITE,
    )
    # 折角内衬：一小块背景色三角，让折角在单色下也读得出来
    draw.polygon(
        [(_s(right - fold), _s(top)), (_s(right - fold), _s(top + fold)), (_s(right), _s(top + fold))],
        fill=ACCENT,
    )
    # 目录线：三段等宽条，末段短一截（像篇名 + 页码）
    for index, (y, x2) in enumerate(((468, 640), (556, 640), (644, 560))):
        draw.rounded_rectangle((_s(384), _s(y - 20), _s(x2), _s(y + 20)), radius=_s(20), fill=ACCENT)
    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    image = render()
    image.save(HERE / "logo-1024.png")
    image.resize((256, 256), Image.LANCZOS).save(HERE / "logo-256.png")
    image.save(HERE / "logo.ico", format="ICO", sizes=ICO_SIZES)
    try:                                   # macOS 打包（flet pack --icon 只吃 .icns）
        image.save(HERE / "logo.icns", format="ICNS")
    except (OSError, ValueError) as exc:   # 老 Pillow 无 ICNS 写支持时只跳过，不拦生成
        print(f"icns skipped: {exc}")
    print(f"logo: {HERE / 'logo-1024.png'}")
    print(f"icon: {HERE / 'logo.ico'} sizes={len(ICO_SIZES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
