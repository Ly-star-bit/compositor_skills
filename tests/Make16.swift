// Make16 <in> <out.tif> <p3|srgb> [orientation]: a 16-bit TIFF as an editor exports one: the image converted by
// ColorSync into Display P3 or sRGB at 16 bits a channel, its profile embedded, and with an EXIF orientation tag
// (the pixels stay as stored; a reader turns them upright). Headless: Core Graphics and ImageIO only.
import Foundation
import CoreGraphics
import ImageIO

let args = CommandLine.arguments
guard args.count >= 4, let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: args[1]) as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
      let space = CGColorSpace(name: args[3] == "p3" ? CGColorSpace.displayP3 : CGColorSpace.sRGB) else {
    FileHandle.standardError.write("usage: Make16 <in> <out.tif> <p3|srgb> [orientation]\n".data(using: .utf8)!)
    exit(2)
}
let w = image.width, h = image.height
let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 16, bytesPerRow: w * 8, space: space,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder16Little.rawValue)!
context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: args[2]) as CFURL, "public.tiff" as CFString, 1, nil)!
let properties: [CFString: Any] = args.count > 4 ? [kCGImagePropertyOrientation: Int(args[4]) ?? 1] : [:]
CGImageDestinationAddImage(destination, context.makeImage()!, properties as CFDictionary)
exit(CGImageDestinationFinalize(destination) ? 0 : 1)
