// AdjustReference: applies one adjustment layer's settings to raw RGBA pixels with Compositor's own code, so the
// tests can check retouch_builder.py's ports. usage: AdjustReference <adjustment.json> <w> <h> <in.rgba> <out.rgba>
// Everything above the tool's MARK is copied from github.com/robbietilton/Compositor at b4bfdea: Curves.swift,
// Levels.swift, ImageAdjustments.swift and HueSaturation.swift (the parts that turn settings into tables), with
// the image plumbing and caching left out; the pixel kernels are in AdjustReference.c (Tonal Contrast's from
// AdjustPixels.c at 6d49a239). Tonal Contrast and Sharpen also run their Core Image steps, as Filters.swift does.
//
// MIT License
//
// Copyright (c) 2026 Wonder Assembly LLC
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
import CoreImage
import Foundation

struct CurvePoint: Codable, Equatable {
    var x: Double
    var y: Double
}
struct CurvesSettings: Codable, Equatable {
    var channel = LevelsChannel.rgb
    var channels = Array(repeating: [CurvePoint(x: 0, y: 0), CurvePoint(x: 255, y: 255)], count: 4)
    var isValid: Bool {
        channels.count == 4 && channels.allSatisfy { points in
            (2...32).contains(points.count) && points.first?.x == 0 && points.last?.x == 255
            && points.allSatisfy { $0.x.isFinite && $0.y.isFinite && (0...255).contains($0.x) && (0...255).contains($0.y) }
            && zip(points, points.dropFirst()).allSatisfy { $0.x < $1.x }
        }
    }
    /// Shape-preserving cubic Hermite interpolation avoids overshoot between handles.
    func value(_ x: Double, channel: Int) -> Double {
        let p = channels[channel]
        let i = min(p.count - 2, max(0, p.lastIndex(where: { $0.x <= x }) ?? 0))
        let d = zip(p, p.dropFirst()).map { ($1.y - $0.y) / ($1.x - $0.x) }
        func slope(_ j: Int) -> Double {
            if j == 0 { return d[0] }
            if j == p.count-1 { return d.last! }
            if d[j-1] * d[j] <= 0 { return 0 }
            return 2 / (1/d[j-1] + 1/d[j])
        }
        let h = p[i+1].x-p[i].x, t = min(1, max(0, (x-p[i].x)/h))
        let y = (2*t*t*t-3*t*t+1)*p[i].y + (t*t*t-2*t*t+t)*h*slope(i)
            + (-2*t*t*t+3*t*t)*p[i+1].y + (t*t*t-t*t)*h*slope(i+1)
        return min(255, max(0, y))
    }
}

enum LevelsChannel: String, CaseIterable, Codable {
    case rgb = "RGB", red = "Red", green = "Green", blue = "Blue"
    var index: Int { Self.allCases.firstIndex(of: self)! }
}
struct LevelRange: Equatable, Codable {
    var black: Double = 0
    var gamma: Double = 1
    var white: Double = 255
    var outputBlack: Double = 0
    var outputWhite: Double = 255
    var normalized: Self {
        func clamp(_ n: Double, _ range: ClosedRange<Double>, _ fallback: Double) -> Double {
            n.isFinite ? min(range.upperBound, max(range.lowerBound, n)) : fallback
        }
        var result = self
        result.black = clamp(black, 0...254, 0)
        result.white = clamp(white, (result.black + 1)...255, 255)
        result.gamma = clamp(gamma, 0.1...9.99, 1)
        result.outputBlack = clamp(outputBlack, 0...255, 0)
        result.outputWhite = clamp(outputWhite, 0...255, 255)
        return result
    }
    func apply(_ value: Double) -> Double {
        let s = normalized
        let input = min(1, max(0, (value * 255 - s.black) / (s.white - s.black)))
        return (s.outputBlack + pow(input, 1 / s.gamma) * (s.outputWhite - s.outputBlack)) / 255
    }
}
struct LevelsSettings: Equatable, Codable {
    var channel: LevelsChannel = .rgb
    var ranges = Array(repeating: LevelRange(), count: 4)
    var current: LevelRange {
        get { ranges[channel.index] }
        set { ranges[channel.index] = newValue.normalized }
    }
    var isIdentity: Bool { ranges.allSatisfy { $0.normalized == LevelRange() } }
    /// Individual channels, followed by the composite RGB adjustment.
    func apply(_ value: Double, channel: LevelsChannel) -> Double {
        ranges[0].apply(ranges[channel.index].apply(value))
    }
}

enum ImageAdjustmentPixels {
    static func clamp(_ value: Double, _ range: ClosedRange<Double>, _ fallback: Double) -> Double {
        value.isFinite ? min(range.upperBound, max(range.lowerBound, value)) : fallback
    }
}

struct ExposureSettings: Codable, Equatable {
    static let exposureRange: ClosedRange<Double> = -20...20
    static let offsetRange: ClosedRange<Double> = -0.5...0.5
    static let gammaRange: ClosedRange<Double> = 0.01...9.99
    /// Stops of light, −20…20.
    var exposure: Double = 0
    /// Added in linear light, −0.5…0.5: negative deepens the shadows, positive lifts them.
    var offset: Double = 0
    /// Gamma correction, 0.01…9.99; above 1 brightens the midtones.
    var gamma: Double = 1
    var isValid: Bool { Self.exposureRange.contains(exposure) && Self.offsetRange.contains(offset) && Self.gammaRange.contains(gamma) }
    var normalized: Self {
        Self(exposure: ImageAdjustmentPixels.clamp(exposure, Self.exposureRange, 0),
             offset: ImageAdjustmentPixels.clamp(offset, Self.offsetRange, 0),
             gamma: ImageAdjustmentPixels.clamp(gamma, Self.gammaRange, 1))
    }
    /// Each channel's output (0–1) for each input byte, decoded to linear light and encoded back.
    var table: [Float] {
        let scale = pow(2, exposure)
        return (0...255).map { index in
            let encoded = Double(index) / 255
            var linear = encoded <= 0.04045 ? encoded / 12.92 : pow((encoded + 0.055) / 1.055, 2.4)
            linear = pow(max(0, linear * scale + offset), 1 / gamma)
            let output = linear <= 0.0031308 ? linear * 12.92 : 1.055 * pow(linear, 1 / 2.4) - 0.055
            return Float(min(1, max(0, output)))
        }
    }
}

struct AdjustmentColor: Codable, Equatable {
    var red: Double
    var green: Double
    var blue: Double
    init(red: Double, green: Double, blue: Double) {
        self.red = red; self.green = green; self.blue = blue
    }
    var isValid: Bool { [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) } }
    var clamped: Self {
        Self(red: ImageAdjustmentPixels.clamp(red, 0...1, 0), green: ImageAdjustmentPixels.clamp(green, 0...1, 0),
             blue: ImageAdjustmentPixels.clamp(blue, 0...1, 0))
    }
}

struct GradientMapSettings: Codable, Equatable {
    var shadows = AdjustmentColor(red: 0, green: 0, blue: 0)
    var highlights = AdjustmentColor(red: 1, green: 1, blue: 1)
    var reversed = false
    var isValid: Bool { shadows.isValid && highlights.isValid }
    var normalized: Self {
        var result = self
        result.shadows = shadows.clamped
        result.highlights = highlights.clamped
        return result
    }
    /// The colors for the darkest and lightest tones, in the order they apply.
    var ends: (dark: AdjustmentColor, light: AdjustmentColor) { reversed ? (highlights, shadows) : (shadows, highlights) }
}

struct BlackWhiteSettings: Codable, Equatable {
    static let range: ClosedRange<Double> = -200...300
    /// Photoshop's defaults.
    var reds: Double = 40
    var yellows: Double = 60
    var greens: Double = 40
    var cyans: Double = 60
    var blues: Double = 20
    var magentas: Double = 80
    /// Color the result while keeping its tones, for a sepia or a cyanotype.
    var tint = false
    var tintHue: Double = 40
    var tintSaturation: Double = 20
    var isValid: Bool {
        [reds, yellows, greens, cyans, blues, magentas].allSatisfy { $0.isFinite && Self.range.contains($0) }
            && tintHue.isFinite && (0...360).contains(tintHue)
            && tintSaturation.isFinite && (0...100).contains(tintSaturation)
    }
}

struct ColorBalanceSettings: Codable, Equatable {
    static let range: ClosedRange<Double> = -100...100
    var shadowCyanRed: Double = 0
    var shadowMagentaGreen: Double = 0
    var shadowYellowBlue: Double = 0
    var midCyanRed: Double = 0
    var midMagentaGreen: Double = 0
    var midYellowBlue: Double = 0
    var highlightCyanRed: Double = 0
    var highlightMagentaGreen: Double = 0
    var highlightYellowBlue: Double = 0
    var preserveLuminosity = true
    private var all: [Double] {
        [shadowCyanRed, shadowMagentaGreen, shadowYellowBlue,
         midCyanRed, midMagentaGreen, midYellowBlue,
         highlightCyanRed, highlightMagentaGreen, highlightYellowBlue]
    }
    var isValid: Bool { all.allSatisfy { $0.isFinite && Self.range.contains($0) } }
    var isIdentity: Bool { all.allSatisfy { $0 == 0 } }
}

struct GrainSettings: Codable, Equatable {
    static let amountRange: ClosedRange<Double> = 0...100
    static let sizeRange: ClosedRange<Double> = 0.5...20
    static let roughnessRange: ClosedRange<Double> = 0...100
    /// Strength, 0–100.
    var amount: Double = 25
    /// Grain scale in document pixels, 0.5–20.
    var size: Double = 1.5
    /// 0–100: how much smaller, irregular detail roughens the main grain particles.
    var roughness: Double = 50
    var seed: UInt32 = 0
    var isValid: Bool { Self.amountRange.contains(amount) && Self.sizeRange.contains(size) && Self.roughnessRange.contains(roughness) }
    var normalized: Self {
        var result = self
        result.amount = ImageAdjustmentPixels.clamp(amount, Self.amountRange, 25)
        result.size = ImageAdjustmentPixels.clamp(size, Self.sizeRange, 1.5)
        result.roughness = ImageAdjustmentPixels.clamp(roughness, Self.roughnessRange, 50)
        return result
    }
    /// `origin` and `unitsPerPixel` place the image's pixels in document space (a whole layer at 1:1 is
    /// origin zero, one unit per pixel); `seed` replaces the stored pattern when given.
}

enum ColorRange: String, CaseIterable, Hashable, Codable {
    case master = "Master", reds = "Reds", yellows = "Yellows", greens = "Greens"
    case cyans = "Cyans", blues = "Blues", magentas = "Magentas"

    /// Photoshop's starting hue band: falloff start, range start, range end, falloff end.
    var defaultBand: HueBand {
        switch self {
        case .master: HueBand(falloffStart: 0, rangeStart: 0, rangeEnd: 360, falloffEnd: 360)
        case .reds: HueBand(falloffStart: 315, rangeStart: 345, rangeEnd: 15, falloffEnd: 45)
        case .yellows: HueBand(falloffStart: 15, rangeStart: 45, rangeEnd: 75, falloffEnd: 105)
        case .greens: HueBand(falloffStart: 75, rangeStart: 105, rangeEnd: 135, falloffEnd: 165)
        case .cyans: HueBand(falloffStart: 135, rangeStart: 165, rangeEnd: 195, falloffEnd: 225)
        case .blues: HueBand(falloffStart: 195, rangeStart: 225, rangeEnd: 255, falloffEnd: 285)
        case .magentas: HueBand(falloffStart: 255, rangeStart: 285, rangeEnd: 315, falloffEnd: 345)
        }
    }
    static let colorRanges = ColorRange.allCases.filter { $0 != .master }
}


struct HueBand: Equatable, Codable {
    var falloffStart: Double
    var rangeStart: Double
    var rangeEnd: Double
    var falloffEnd: Double

    /// Degrees from `from` forward to `to`, always 0…360.
    static func forward(_ from: Double, _ to: Double) -> Double {
        let delta = (to - from).truncatingRemainder(dividingBy: 360)
        return delta < 0 ? delta + 360 : delta
    }

    /// How strongly this band claims a hue: 1 inside the range, ramping linearly through
    /// each falloff shoulder, 0 outside. Wraparound is handled by measuring forward.
    func weight(of hue: Double) -> Double {
        let span = Self.forward(falloffStart, falloffEnd)
        guard span > 0 else { return 1 } // Master covers everything.
        let position = Self.forward(falloffStart, hue)
        guard position <= span else { return 0 }
        let rampIn = Self.forward(falloffStart, rangeStart)
        let plateauEnd = Self.forward(falloffStart, rangeEnd)
        if position < rampIn { return rampIn > 0 ? position / rampIn : 1 }
        if position <= plateauEnd { return 1 }
        let rampOut = span - plateauEnd
        return rampOut > 0 ? (span - position) / rampOut : 1
    }

}

struct RangeAdjustment: Equatable, Codable {
    var hue: Double = 0
    var saturation: Double = 0
    var lightness: Double = 0
}

struct HueSaturationSettings: Equatable, Codable {
    /// Which range the sliders and spectrum edit.
    var range: ColorRange = .master
    var colorize = false
    /// Applies the selected range to everything *outside* its band instead.
    var invertRange = false
    var adjustments: [ColorRange: RangeAdjustment] = [:]
    var bands: [ColorRange: HueBand] = Dictionary(uniqueKeysWithValues: ColorRange.allCases.map { ($0, $0.defaultBand) })

    init(hue: Double = 0, saturation: Double = 0, lightness: Double = 0, colorize: Bool = false,
         range: ColorRange = .master) {
        self.range = range
        self.colorize = colorize
        adjustments[range] = RangeAdjustment(hue: hue, saturation: saturation, lightness: lightness)
    }

    /// The sliders read and write the selected range.
    var hue: Double {
        get { adjustments[range]?.hue ?? 0 }
        set { adjustments[range, default: RangeAdjustment()].hue = newValue }
    }
    var saturation: Double {
        get { adjustments[range]?.saturation ?? 0 }
        set { adjustments[range, default: RangeAdjustment()].saturation = newValue }
    }
    var lightness: Double {
        get { adjustments[range]?.lightness ?? 0 }
        set { adjustments[range, default: RangeAdjustment()].lightness = newValue }
    }
    var band: HueBand {
        get { bands[range] ?? range.defaultBand }
        set { bands[range] = newValue }
    }

    /// Photoshop's starting point when Colorize is switched on.
    static let colorizeStart = HueSaturationSettings(hue: 0, saturation: 25, lightness: 0, colorize: true)
    var isIdentity: Bool { !colorize && adjustments.values.allSatisfy { $0 == RangeAdjustment() } }

    /// How much a range applies to one hue: Master everywhere, others through their band.
    func weight(of colorRange: ColorRange, hue: Double) -> Double {
        guard colorRange != .master else { return 1 }
        let weight = (bands[colorRange] ?? colorRange.defaultBand).weight(of: hue)
        return invertRange && colorRange == range ? 1 - weight : weight
    }
}


enum HueSaturationFilter {
    /// 33 points per axis, the usual size for this kind of lookup: fast to build, smooth enough.
    static let dimension = 33

    /// How much every range shifts a given hue, sampled once per degree. Building this
    /// once per settings keeps the cube cheap: without it each of ~36k cube entries would
    /// re-evaluate all seven ranges.
    typealias HueResponse = (shift: Double, saturation: Double, lightness: Double)

    static func hueResponse(_ settings: HueSaturationSettings) -> [HueResponse] {
        (0...360).map { degree in
            var response: HueResponse = (0, 0, 0)
            for (colorRange, adjustment) in settings.adjustments where adjustment != RangeAdjustment() {
                let weight = settings.weight(of: colorRange, hue: Double(degree))
                guard weight > 0 else { continue }
                response.shift += adjustment.hue * weight
                response.saturation += adjustment.saturation * weight
                response.lightness += adjustment.lightness * weight
            }
            return response
        }
    }

    static func buildCube(_ settings: HueSaturationSettings) -> Data {
        let response = hueResponse(settings)
        var values = [Float](repeating: 0, count: dimension * dimension * dimension * 4)
        var index = 0
        let step = Double(dimension - 1)
        for blue in 0..<dimension {
            for green in 0..<dimension {
                for red in 0..<dimension {
                    let color = adjust(red: Double(red) / step, green: Double(green) / step, blue: Double(blue) / step,
                                       settings: settings, response: response)
                    values[index] = Float(color.red)
                    values[index + 1] = Float(color.green)
                    values[index + 2] = Float(color.blue)
                    values[index + 3] = 1
                    index += 4
                }
            }
        }
        return values.withUnsafeBufferPointer { Data(buffer: $0) }
    }

    static func adjust(red: Double, green: Double, blue: Double, settings: HueSaturationSettings,
                       response: [HueResponse]? = nil) -> (red: Double, green: Double, blue: Double) {
        var (hue, saturation, lightness) = toHSL(red: red, green: green, blue: blue)
        var lightnessAmount = 0.0
        if settings.colorize {
            hue = settings.hue.truncatingRemainder(dividingBy: 360)
            saturation = min(1, max(0, settings.saturation / 100))
            lightnessAmount = settings.lightness / 100
        } else {
            // Every range contributes, weighted by how strongly it claims the original hue.
            let table = response ?? hueResponse(settings)
            let sampled = table[min(table.count - 1, max(0, Int(hue.rounded())))]
            lightnessAmount = sampled.lightness / 100
            hue = (hue + sampled.shift).truncatingRemainder(dividingBy: 360)
            if hue < 0 { hue += 360 }
            saturation = adjustedSaturation(saturation, by: sampled.saturation)
        }
        // Lightness pulls toward white above 0 and toward black below, reaching either at ±100.
        let amount = min(1, max(-1, lightnessAmount))
        lightness = amount >= 0 ? lightness + (1 - lightness) * amount : lightness * (1 + amount)
        return toRGB(hue: hue, saturation: saturation, lightness: min(1, max(0, lightness)))
    }

    /// The hue a spectrum swatch becomes, for the "after" bar.
    /// Photoshop's Saturation: below 0 it scales toward gray (−100 is gray); above 0 it divides by what's left, so
    /// +50 doubles it and +100 takes any color all the way. Multiplicative both ways, so neutral grays stay neutral.
    static func adjustedSaturation(_ saturation: Double, by amount: Double) -> Double {
        let amount = min(1, max(-1, amount / 100))
        guard amount > 0 else { return max(0, saturation * (1 + amount)) }
        return amount >= 1 ? (saturation > 0 ? 1 : 0) : min(1, saturation / (1 - amount))
    }

    static func shiftedHue(_ hue: Double, settings: HueSaturationSettings) -> Double {
        var shift = 0.0
        for (colorRange, adjustment) in settings.adjustments where adjustment.hue != 0 {
            shift += adjustment.hue * settings.weight(of: colorRange, hue: hue)
        }
        let shifted = (hue + shift).truncatingRemainder(dividingBy: 360)
        return shifted < 0 ? shifted + 360 : shifted
    }

    private static func toHSL(red: Double, green: Double, blue: Double) -> (Double, Double, Double) {
        let high = max(red, green, blue), low = min(red, green, blue)
        let lightness = (high + low) / 2
        let delta = high - low
        guard delta > 0 else { return (0, 0, lightness) }
        let saturation = delta / (1 - abs(2 * lightness - 1))
        var hue: Double
        if high == red { hue = (green - blue) / delta }
        else if high == green { hue = (blue - red) / delta + 2 }
        else { hue = (red - green) / delta + 4 }
        hue *= 60
        if hue < 0 { hue += 360 }
        return (hue, min(1, saturation), lightness)
    }

    private static func toRGB(hue: Double, saturation: Double, lightness: Double)
        -> (red: Double, green: Double, blue: Double) {
        guard saturation > 0 else { return (lightness, lightness, lightness) }
        let chroma = (1 - abs(2 * lightness - 1)) * saturation
        let sector = hue / 60
        let second = chroma * (1 - abs(sector.truncatingRemainder(dividingBy: 2) - 1))
        let base = lightness - chroma / 2
        let (red, green, blue): (Double, Double, Double)
        switch Int(sector) {
        case 0: (red, green, blue) = (chroma, second, 0)
        case 1: (red, green, blue) = (second, chroma, 0)
        case 2: (red, green, blue) = (0, chroma, second)
        case 3: (red, green, blue) = (0, second, chroma)
        case 4: (red, green, blue) = (second, 0, chroma)
        default: (red, green, blue) = (chroma, 0, second)
        }
        return (min(1, max(0, red + base)), min(1, max(0, green + base)), min(1, max(0, blue + base)))
    }
}


// MARK: - The tool (not from Compositor): one adjustment, on raw opaque RGBA bytes, through the code above.

struct Adjustment: Decodable {
    let kind: String
    var hue: Double? = nil, saturation: Double? = nil, lightness: Double? = nil, colorize: Bool? = nil
    var levels: LevelsSettings? = nil
    var curves: CurvesSettings? = nil
    var exposureSettings: ExposureSettings? = nil
    var hsvSettings: HueSaturationSettings? = nil
    var gradientMapSettings: GradientMapSettings? = nil
    var blackWhiteSettings: BlackWhiteSettings? = nil
    var colorBalanceSettings: ColorBalanceSettings? = nil
    var grainSettings: GrainSettings? = nil
    var filterLayerSettings: FilterLayerSettings? = nil
    var blurRadius: Double? = nil
    var preserveTransparency: Bool? = nil
}

// The Core Image steps of the filter adjustment layers (Filters.swift `run`, PixelAdjust.render): no color
// management, 8-bit premultiplied sRGB in and out, as Compositor renders them. Nothing is shown: a CIContext in a
// command-line tool draws to memory.
struct TonalLayerSettings: Decodable { var amount: Double; var radius: Double; var shadows: Double; var midtones: Double; var highlights: Double }
struct SharpenLayerSettings: Decodable { var amount: Double; var radius: Double }
struct FilterLayerSettings: Decodable { var tonal: TonalLayerSettings?; var sharpen: SharpenLayerSettings? }

let ciContext = CIContext(options: [.workingColorSpace: NSNull(), .outputColorSpace: NSNull()])
let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!
let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue

func cgImage(_ rgba: UnsafeMutablePointer<UInt8>, width: Int, height: Int) -> CGImage {
    let data = Data(bytes: rgba, count: width * height * 4)
    return CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4, space: sRGB,
                   bitmapInfo: CGBitmapInfo(rawValue: bitmapInfo), provider: CGDataProvider(data: data as CFData)!,
                   decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
}

/// `image` rendered to RGBA8 premultiplied bytes, top-down rows (PixelAdjust.render, then BrushRaster.draw).
func rendered(_ image: CIImage, width: Int, height: Int) -> [UInt8] {
    let extent = CGRect(x: 0, y: 0, width: width, height: height)
    let drawn = ciContext.createCGImage(image, from: extent, format: .RGBA8, colorSpace: sRGB)!
    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    bytes.withUnsafeMutableBytes { raw in
        let context = CGContext(data: raw.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                space: sRGB, bitmapInfo: bitmapInfo)!
        context.interpolationQuality = .none
        context.draw(drawn, in: extent)
    }
    return bytes
}


let arguments = CommandLine.arguments
guard arguments.count == 6, let width = Int(arguments[2]), let height = Int(arguments[3]) else {
    FileHandle.standardError.write("usage: AdjustReference <adjustment.json> <width> <height> <in.rgba> <out.rgba>\n".data(using: .utf8)!)
    exit(2)
}
let adjustment = try JSONDecoder().decode(Adjustment.self, from: Data(contentsOf: URL(fileURLWithPath: arguments[1])))
var pixels = [UInt8](try Data(contentsOf: URL(fileURLWithPath: arguments[4])))
precondition(pixels.count == width * height * 4)
pixels.withUnsafeMutableBufferPointer { buffer in
    let p = buffer.baseAddress!, count = width * height, stride = width * 4
    switch adjustment.kind {
    case "Curves":
        let curves = adjustment.curves!
        let table = (1...3).flatMap { channel in (0...255).map { Float(curves.value(curves.value(Double($0), channel: channel), channel: 0) / 255) } }
        levels_apply(p, count, table)
    case "Levels":
        let settings = adjustment.levels!
        let tables = [LevelsChannel.red, .green, .blue].flatMap { channel in (0...255).map { Float(settings.apply(Double($0) / 255, channel: channel)) } }
        levels_apply(p, count, tables)
    case "Exposure":
        levels_apply(p, count, Array([[Float]](repeating: adjustment.exposureSettings!.table, count: 3).joined()))
    case "Hue/Saturation":
        let settings = adjustment.hsvSettings ?? HueSaturationSettings(hue: adjustment.hue ?? 0, saturation: adjustment.saturation ?? 0,
                                                                       lightness: adjustment.lightness ?? 0, colorize: adjustment.colorize ?? false)
        HueSaturationFilter.buildCube(settings).withUnsafeBytes { raw in
            cube_apply(p, count, raw.assumingMemoryBound(to: Float.self).baseAddress!, Int32(HueSaturationFilter.dimension))
        }
    case "Color Balance":
        let s = adjustment.colorBalanceSettings!
        guard !s.isIdentity else { break }
        adjust_color_balance(p, width, height, stride,
                             [s.shadowCyanRed, s.shadowMagentaGreen, s.shadowYellowBlue].map { Float($0 / 100) },
                             [s.midCyanRed, s.midMagentaGreen, s.midYellowBlue].map { Float($0 / 100) },
                             [s.highlightCyanRed, s.highlightMagentaGreen, s.highlightYellowBlue].map { Float($0 / 100) },
                             s.preserveLuminosity ? 1 : 0)
    case "Black & White":
        let s = adjustment.blackWhiteSettings!
        adjust_black_white(p, width, height, stride, [s.reds, s.yellows, s.greens, s.cyans, s.blues, s.magentas].map { Float($0 / 100) },
                           s.tint ? 1 : 0, s.tintHue, s.tintSaturation / 100)
    case "Gradient Map":
        let settings = adjustment.gradientMapSettings!
        let ends = settings.ends
        func gradientTable() -> [UInt8] {
            let (dark, light) = ends
            // Split into explicitly typed steps: as one expression the type checker times out (Xcode 26.1).
            func channel(_ from: Double, _ to: Double, _ t: Double) -> UInt8 {
                let value: Double = from + (to - from) * t
                let scaled: Double = (value * 255).rounded()
                return UInt8(min(255.0, max(0.0, scaled)))
            }
            var table = [UInt8]()
            table.reserveCapacity(256 * 3)
            for index in 0...255 {
                let t: Double = Double(index) / 255
                table.append(channel(dark.red, light.red, t))
                table.append(channel(dark.green, light.green, t))
                table.append(channel(dark.blue, light.blue, t))
            }
            return table
        }
        adjust_gradient_map(p, width, height, stride, gradientTable())
    case "Grain":
        let s = adjustment.grainSettings!
        adjust_grain(p, width, height, stride, s.amount, s.size, s.roughness, s.seed, 0, 0, 1)
    case "Tonal Contrast":
        let s = adjustment.filterLayerSettings?.tonal ?? TonalLayerSettings(amount: 50, radius: 16, shadows: 40, midtones: 60, highlights: 30)
        // Not clamped: beyond the layer the blur sees transparency, so near the edges the base thins out.
        let blurred = CIImage(cgImage: cgImage(p, width: width, height: height)).applyingGaussianBlur(sigma: s.radius)
        var base = rendered(blurred.cropped(to: CGRect(x: 0, y: 0, width: width, height: height)), width: width, height: height)
        base.withUnsafeMutableBufferPointer { b in
            adjust_tonal_contrast(p, b.baseAddress!, width, height, stride, stride, s.amount, s.shadows, s.midtones, s.highlights)
        }
    case "Gaussian Blur":
        // The adjustment layer's blur (LayerAdjustment.apply -> PixelFilter.run .gaussianBlur): the edges carried on
        // past every side, as for Sharpen; with `preserveTransparency` the layer is blurred unclamped instead.
        let extent = CGRect(x: 0, y: 0, width: width, height: height)
        let source = CIImage(cgImage: cgImage(p, width: width, height: height))
        let blurred = (adjustment.preserveTransparency == true ? source : source.clampedToExtent())
            .applyingGaussianBlur(sigma: adjustment.blurRadius ?? 10)
        let out = rendered(blurred.cropped(to: extent), width: width, height: height)
        out.withUnsafeBufferPointer { memcpy(p, $0.baseAddress!, count * 4) }
    case "Sharpen":
        let s = adjustment.filterLayerSettings?.sharpen ?? SharpenLayerSettings(amount: 100, radius: 1)
        // Edge pixels carry on past every side (FilterEdit.extending with all the canvas's sides).
        let extent = CGRect(x: 0, y: 0, width: width, height: height)
        let sharpened = CIImage(cgImage: cgImage(p, width: width, height: height)).clampedToExtent()
            .applyingFilter("CIUnsharpMask", parameters: [kCIInputRadiusKey: s.radius, kCIInputIntensityKey: s.amount / 100])
        let out = rendered(sharpened.cropped(to: extent), width: width, height: height)
        out.withUnsafeBufferPointer { memcpy(p, $0.baseAddress!, count * 4) }
    default:
        FileHandle.standardError.write("not covered: \(adjustment.kind)\n".data(using: .utf8)!)
        exit(3)
    }
}
try Data(pixels).write(to: URL(fileURLWithPath: arguments[5]))
