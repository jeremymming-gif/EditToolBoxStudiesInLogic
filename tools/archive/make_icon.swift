// 生成 App 图标（纯 CoreGraphics 绘制，不依赖设计素材）
// 用法: swiftc make_icon.swift -o make_icon && ./make_icon <输出目录>
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

func roundedRect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat) -> CGPath {
    CGPath(roundedRect: CGRect(x: x, y: y, width: w, height: h),
           cornerWidth: r, cornerHeight: r, transform: nil)
}

func renderIcon(size: Int) -> CGImage? {
    let s = CGFloat(size)
    let cs = CGColorSpaceCreateDeviceRGB()

    guard let ctx = CGContext(data: nil, width: size, height: size,
                              bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        return nil
    }
    ctx.setShouldAntialias(true)
    ctx.interpolationQuality = .high

    // ---------- 底板：圆角方形 + 渐变 ----------
    let inset = s * 0.055
    let board = CGRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)

    ctx.saveGState()
    ctx.addPath(roundedRect(board.minX, board.minY, board.width, board.height, board.width * 0.2237))
    ctx.clip()

    let top = CGColor(red: 0.40, green: 0.52, blue: 0.95, alpha: 1)
    let bottom = CGColor(red: 0.15, green: 0.21, blue: 0.60, alpha: 1)
    if let grad = CGGradient(colorsSpace: cs, colors: [top, bottom] as CFArray, locations: [0, 1]) {
        ctx.drawLinearGradient(grad,
                               start: CGPoint(x: board.minX, y: board.maxY),
                               end: CGPoint(x: board.maxX, y: board.minY),
                               options: [])
    }
    ctx.restoreGState()

    // ---------- 右侧：合并后的文档 ----------
    let docX = s * 0.375, docY = s * 0.175
    let docW = s * 0.455, docH = s * 0.650
    let docR = s * 0.045

    // 轻微阴影，让白纸浮起来
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -s * 0.012),
                  blur: s * 0.030,
                  color: CGColor(red: 0, green: 0, blue: 0, alpha: 0.28))
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.addPath(roundedRect(docX, docY, docW, docH, docR))
    ctx.fillPath()
    ctx.restoreGState()

    // 文档上的文字线
    let lineColor = CGColor(red: 0.42, green: 0.47, blue: 0.62, alpha: 1)
    ctx.setFillColor(lineColor)
    let lines: [(CGFloat, CGFloat)] = [(0.72, 0.62), (0.60, 0.47), (0.68, 0.32)]
    for (wRatio, yRatio) in lines {
        let lw = docW * wRatio
        let ly = docY + docH * yRatio
        ctx.addPath(roundedRect(docX + docW * 0.145, ly, lw, s * 0.040, s * 0.020))
        ctx.fillPath()
    }

    // ---------- 左侧：待合并的三份文件 ----------
    let barX = s * 0.100
    let barW = s * 0.215
    let barH = s * 0.088
    let barR = s * 0.026
    let barYs: [CGFloat] = [0.690, 0.456, 0.222]
    let alphas: [CGFloat] = [0.60, 0.80, 1.0]

    for (i, yRatio) in barYs.enumerated() {
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: alphas[i]))
        ctx.addPath(roundedRect(barX, s * yRatio, barW, barH, barR))
        ctx.fillPath()
    }

    // ---------- 箭头：三条汇聚成一条 ----------
    ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.95))
    ctx.setLineWidth(s * 0.030)
    ctx.setLineCap(.round)
    ctx.setLineJoin(.round)

    let jointX = s * 0.335
    let targetY = s * 0.410
    for yRatio in barYs {
        let fromY = s * yRatio + barH / 2
        ctx.move(to: CGPoint(x: barX + barW, y: fromY))
        ctx.addLine(to: CGPoint(x: jointX, y: fromY))
        ctx.addLine(to: CGPoint(x: docX, y: targetY + barH / 2))
        ctx.strokePath()
    }

    guard let image = ctx.makeImage() else { return nil }
    return image
}

func write(_ image: CGImage, to path: String) {
    let url = URL(fileURLWithPath: path)
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        return
    }
    CGImageDestinationAddImage(dest, image, nil)
    CGImageDestinationFinalize(dest)
}

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

// iconset 需要的全部尺寸
let specs: [(Int, String)] = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]

for (size, name) in specs {
    guard let image = renderIcon(size: size) else {
        print("渲染失败: \(name)")
        continue
    }
    write(image, to: (outDir as NSString).appendingPathComponent(name))
}

// 额外存一张 1024 预览图
if let big = renderIcon(size: 1024) {
    write(big, to: (outDir as NSString).appendingPathComponent("preview_1024.png"))
}

print("图标已生成到 \(outDir)")
