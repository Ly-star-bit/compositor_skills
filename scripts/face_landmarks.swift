// face_landmarks.swift - prints the faces Apple Vision finds in an image, with their landmarks, as JSON, and with
// `--matte <out.png>` also writes Vision's person segmentation (white = people) as an 8-bit grayscale PNG.
// `--import <image> <out.png>` instead writes the image as a 16-bit sRGB PNG, upright (see importImage).
// retouch_builder.py builds it once (swiftc) and runs it as a plain command-line tool: no window, no app.
// Points are in the image's pixels with the origin at the top left.
import CoreGraphics
import CoreImage
import Foundation
import ImageIO
import Vision

struct Face: Encodable {
    let box: [Double]  // x, y, width, height
    let confidence: Double
    let roll: Double?
    let yaw: Double?
    let landmarks: [String: [[Double]]]
}

struct Output: Encodable {
    let width: Int
    let height: Int
    let faces: [Face]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

/// Writes a one-channel 8-bit pixel buffer as a grayscale PNG, values as they are (no color management).
func writeGray(_ buffer: CVPixelBuffer, to path: String) -> Bool {
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
    let width = CVPixelBufferGetWidth(buffer), height = CVPixelBufferGetHeight(buffer)
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    guard let base = CVPixelBufferGetBaseAddress(buffer) else { return false }
    let data = Data(bytes: base, count: stride * height)
    guard let provider = CGDataProvider(data: data as CFData),
          let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: stride,
                              space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: 0),
                              provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent),
          let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL,
                                                            "public.png" as CFString, 1, nil) else { return false }
    CGImageDestinationAddImage(destination, image, nil)
    return CGImageDestinationFinalize(destination)
}

/// A photo of more than 8 bits a channel (16-bit or float) as a 16-bit layer: decoded at its full depth, turned upright
/// by its EXIF orientation and converted from its own color profile to sRGB by ColorSync, as Compositor imports one.
/// Python's image libraries decode such files only in part, and can't convert their profiles at that depth.
func importImage(_ input: String, to output: String) -> Never {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: input) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldAllowFloat: true] as CFDictionary)
    else { fail("cannot read \(input)") }
    let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any] ?? [:]
    let orientation = (properties[kCGImagePropertyOrientation] as? NSNumber)?.int32Value ?? 1
    let upright = CIImage(cgImage: image).oriented(forExifOrientation: orientation)
    guard let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let linear = CGColorSpace(name: CGColorSpace.extendedLinearSRGB) else { fail("no sRGB color space") }
    let context = CIContext(options: [.workingColorSpace: linear, .workingFormat: CIFormat.RGBAf, .cacheIntermediates: false])
    guard let result = context.createCGImage(upright, from: upright.extent, format: .RGBA16, colorSpace: srgb),
          let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: output) as CFURL,
                                                            "public.png" as CFString, 1, nil) else {
        fail("cannot convert \(input)")
    }
    CGImageDestinationAddImage(destination, result, nil)
    guard CGImageDestinationFinalize(destination) else { fail("cannot write \(output)") }
    print("{\"width\": \(result.width), \"height\": \(result.height), \"depth\": \(result.bitsPerComponent)}")
    exit(0)
}

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments.first == "--import" {
    guard arguments.count == 3 else { fail("usage: face_landmarks --import <image> <out.png>") }
    importImage(arguments[1], to: arguments[2])
}
var mattePath: String? = nil
var inputs: [String] = []
var index = 0
while index < arguments.count {
    if arguments[index] == "--matte", index + 1 < arguments.count {
        mattePath = arguments[index + 1]
        index += 2
    } else {
        inputs.append(arguments[index])
        index += 1
    }
}
guard inputs.count == 1 else { fail("usage: face_landmarks <image> [--matte <out.png>]") }
let url = URL(fileURLWithPath: inputs[0])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("cannot read \(url.path)") }
let width = image.width, height = image.height
let request = VNDetectFaceLandmarksRequest()
var requests: [VNRequest] = [request]
var segmentation: VNRequest? = nil
if mattePath != nil, #available(macOS 12.0, *) {
    let people = VNGeneratePersonSegmentationRequest()
    people.qualityLevel = .accurate
    people.outputPixelFormat = kCVPixelFormatType_OneComponent8
    segmentation = people
    requests.append(people)
}
do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform(requests)
} catch {
    fail("Vision could not look for faces: \(error.localizedDescription)")
}
// The matte is a bonus: without it (older macOS, no person found) the faces are still printed.
if let mattePath, #available(macOS 12.0, *),
   let people = segmentation as? VNGeneratePersonSegmentationRequest,
   let buffer = people.results?.first?.pixelBuffer {
    if !writeGray(buffer, to: mattePath) {
        FileHandle.standardError.write(Data("could not write the person matte\n".utf8))
    }
}

let size = CGSize(width: width, height: height)
var faces: [Face] = []
for observation in request.results ?? [] {
    let bounds = observation.boundingBox  // normalized, origin at the bottom left
    let box = [Double(bounds.minX) * Double(width), (1 - Double(bounds.maxY)) * Double(height),
               Double(bounds.width) * Double(width), Double(bounds.height) * Double(height)]
    var landmarks: [String: [[Double]]] = [:]
    if let found = observation.landmarks {
        let regions: [(String, VNFaceLandmarkRegion2D?)] = [
            ("faceContour", found.faceContour), ("leftEye", found.leftEye), ("rightEye", found.rightEye),
            ("leftEyebrow", found.leftEyebrow), ("rightEyebrow", found.rightEyebrow), ("nose", found.nose),
            ("noseCrest", found.noseCrest), ("medianLine", found.medianLine), ("outerLips", found.outerLips),
            ("innerLips", found.innerLips), ("leftPupil", found.leftPupil), ("rightPupil", found.rightPupil),
        ]
        for (name, region) in regions {
            guard let region, region.pointCount > 0 else { continue }
            landmarks[name] = region.pointsInImage(imageSize: size).map { [Double($0.x), Double(height) - Double($0.y)] }
        }
    }
    faces.append(Face(box: box, confidence: Double(observation.confidence), roll: observation.roll?.doubleValue,
                      yaw: observation.yaw?.doubleValue, landmarks: landmarks))
}
guard let json = try? JSONEncoder().encode(Output(width: width, height: height, faces: faces)) else {
    fail("could not encode the result")
}
print(String(decoding: json, as: UTF8.self))
