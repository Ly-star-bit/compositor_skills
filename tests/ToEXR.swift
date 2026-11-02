// ToEXR <in.png> <out.exr> [gain]: an image saved the way Compositor saves an HDR layer (format 13): drawn into half
// floats in extended sRGB and written by ImageIO's OpenEXR encoder, which stores straight linear light. `gain`
// (default 1) scales the sRGB values first, so above 1 the highlights go brighter than white. Headless.
import Foundation
import CoreGraphics
import ImageIO

let args = CommandLine.arguments
guard args.count >= 3, let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: args[1]) as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    FileHandle.standardError.write("usage: ToEXR <in.png> <out.exr> [gain]\n".data(using: .utf8)!)
    exit(2)
}
let gain = args.count > 3 ? Float(args[3]) ?? 1 : 1
let w = image.width, h = image.height
let info = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.floatComponents.rawValue
    | CGBitmapInfo.byteOrder16Little.rawValue
let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 16, bytesPerRow: w * 8,
                        space: CGColorSpace(name: CGColorSpace.extendedSRGB)!, bitmapInfo: info)!
context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
if gain != 1 {
    let pixels = context.data!.assumingMemoryBound(to: Float16.self)
    for i in 0..<(w * h) { for c in 0..<3 { pixels[i * 4 + c] = Float16(Float(pixels[i * 4 + c]) * gain) } }
}
let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: args[2]) as CFURL,
                                                  "com.ilm.openexr-image" as CFString, 1, nil)!
CGImageDestinationAddImage(destination, context.makeImage()!, nil)
exit(CGImageDestinationFinalize(destination) ? 0 : 1)
