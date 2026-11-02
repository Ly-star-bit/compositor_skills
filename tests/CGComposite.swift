// Composites glow layers the way Compositor's ImageExporter + LayerEffectsRenderer do (MIT, b4bfdea): the layer's
// pixels clipped by its mask, recolored by the color overlay with source-atop (alpha kept), then drawn over what is
// below with the layer's opacity and blend mode. Headless: Core Graphics and ImageIO only.
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

struct Spec: Decodable {
    struct Layer: Decodable { let image: String; let mask: String?; let overlay: [Double]?; let opacity: Double }
    let base: String; let out: String; let layers: [Layer]
}
func load(_ path: String) -> CGImage {
    let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil)!
    return CGImageSourceCreateImageAtIndex(source, 0, nil)!
}
let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
func context(_ w: Int, _ h: Int) -> CGContext {
    CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: srgb,
              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
}
let spec = try! JSONDecoder().decode(Spec.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
let base = load(spec.base)
let w = base.width, h = base.height, full = CGRect(x: 0, y: 0, width: w, height: h)
let canvas = context(w, h)
canvas.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)); canvas.fill(full)
canvas.draw(base, in: full)
for layer in spec.layers {
    let styled = context(w, h)
    if let mask = layer.mask { styled.clip(to: full, mask: load(mask)) }   // LayerEffectsRenderer.masked
    styled.draw(load(layer.image), in: full)
    styled.resetClip()
    if let o = layer.overlay {                                             // LayerEffectsRenderer.recolor
        styled.setBlendMode(.sourceAtop)
        styled.setFillColor(CGColor(srgbRed: o[0], green: o[1], blue: o[2], alpha: o[3]))
        styled.fill(full)
    }
    canvas.saveGState()                                                    // LayerRenderer.draw
    canvas.setAlpha(layer.opacity)
    canvas.setBlendMode(.screen)
    canvas.draw(styled.makeImage()!, in: full)
    canvas.restoreGState()
}
let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: spec.out) as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, canvas.makeImage()!, nil)
CGImageDestinationFinalize(dest)
