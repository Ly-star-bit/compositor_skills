// CompValidator: checks .comp packages against Compositor's own load rules, headless.
// usage: CompValidator <project.comp>...   (OK / FAIL per project; exit 1 when any fails)
// The Codable types and validation below are copied from github.com/robbietilton/Compositor: ProjectStore.swift,
// LayerTransform.swift, LayerAppearance.swift, LayerAdjustment.swift, Curves.swift, Levels.swift, HueSaturation.swift,
// ImageAdjustments.swift, LayerGroups.swift, LiveLayerMask.swift, LayerMask.swift and DocumentLimits.swift at commit
// 22c7b8d5, the effect records from LayerEffects.swift at b4bfdea (whose ProjectStore validation matches 22c7b8d5), and
// the format 12/13 image rules (16-bit PNG layers, OpenEXR HDR layers) from ProjectStore.swift at e8c9e29 (2026-10-12).
// Types this skill never writes (text, shape) are stubbed, and errors carry a reason so a failure says which rule
// tripped. Effects out of range fail here, though Compositor loads them (and then doesn't draw them).
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
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

enum DocumentLimits {
    static let maxSide = 30_000
    static let maxSurfacePixels = 200_000_000
    static let documentPixelBudget = min(800_000_000,
        max(maxSurfacePixels, Int(clamping: ProcessInfo.processInfo.physicalMemory / 16)))
}

enum ProjectError: Error, CustomStringConvertible {
    case invalid(String), version(Int), missingImage(String), tooLarge(String)
    var description: String {
        switch self {
        case .invalid(let s): return "invalid: \(s)"
        case .version(let v): return "unsupported version \(v)"
        case .missingImage(let s): return "missingImage: \(s)"
        case .tooLarge(let s): return "tooLarge: \(s)"
        }
    }
}

// MARK: - ProjectStore.swift
struct ProjectManifest: Codable {
    static let current = 20
    static let supported = 1...ProjectManifest.current
    var format = "com.compositor.project"
    var version = ProjectManifest.current
    var colorSpace = "sRGB"
    var resolution: Double? = nil
    let documentID: UUID
    let width: Int
    let height: Int
    let activeLayerID: UUID?
    var layers: [ProjectLayerRecord]
    var guides: [CanvasGuide]? = nil
}

struct ProjectLayerRecord: Codable {
    let id: UUID
    let name: String
    var isVisible: Bool
    let transform: LayerTransform
    let imageFile: String?
    var parentID: UUID? = nil
    var isGroup: Bool? = nil
    var opacity: Double? = nil
    var blendMode: LayerBlendMode? = nil
    var maskFile: String? = nil
    var maskEnabled: Bool? = nil
    var maskSourceID: UUID? = nil
    var adjustment: LayerAdjustment? = nil
    var maskPlacement: LayerTransform? = nil
    var maskLinked: Bool? = nil
    var shape: LayerShapeStyle? = nil
    var effects: LayerEffects? = nil
    var text: LayerTextStyle? = nil
}

// Stubs for metadata this skill never writes.
struct LayerShapeStyle: Codable {}
struct TextRun: Codable {}
struct LayerTextStyle: Codable {
    var colorRuns: [TextRun]? = nil
    var fontRuns: [TextRun]? = nil
    var isValid: Bool { true }
}
struct CanvasGuide: Codable {
    let id: UUID
    let axis: String
    let position: Double
}

// MARK: - LayerEffects.swift (b4bfdea): the Codable effect records, verbatim apart from 'nonisolated'
struct PaletteColor: Equatable { var red: CGFloat; var green: CGFloat; var blue: CGFloat }
/// A line drawn around what the layer shows, outside its edge or inside it.
struct StrokeEffect: Codable, Equatable, Sendable {
    /// Supported document-pixel width; preview work is bounded independently of this value.
    static let maxSize: CGFloat = 500
    var enabled: Bool? = nil // Missing in older projects means visible.
    var isEnabled: Bool { enabled ?? true }
    var size: CGFloat = 4
    var red: CGFloat = 0
    var green: CGFloat = 0
    var blue: CGFloat = 0
    var opacity: Double = 1
    var inside = false
    var color: PaletteColor { PaletteColor(red: red, green: green, blue: blue) }
    var isValid: Bool {
        size.isFinite && (0...StrokeEffect.maxSize).contains(size) && opacity.isFinite && (0...1).contains(opacity)
            && [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) }
    }
}

/// The layer's shape repeated behind it, offset and softened.
struct ShadowEffect: Codable, Equatable, Sendable {
    var enabled: Bool? = nil
    var isEnabled: Bool { enabled ?? true }
    /// Where the light comes from, in degrees counterclockwise from the right, as Photoshop's dial is: 90 is from
    /// straight above, which drops the shadow straight down.
    var angle: CGFloat = 90
    var distance: CGFloat = 20
    var blur: CGFloat = 20
    var red: CGFloat = 0
    var green: CGFloat = 0
    var blue: CGFloat = 0
    var opacity: Double = 0.5
    var color: PaletteColor { PaletteColor(red: red, green: green, blue: blue) }
    /// Where the shadow sits, in layer pixels (y grows downward, as the layer's own pixels do).
    var offset: CGSize {
        let radians = angle * .pi / 180
        // The shadow falls away from the light, and a layer's pixels count y downward.
        return CGSize(width: -cos(radians) * distance, height: sin(radians) * distance)
    }
    var isValid: Bool {
        [angle, distance, blur].allSatisfy(\.isFinite) && (-360...360).contains(angle)
            && (0...5000).contains(distance) && (0...500).contains(blur)
            && opacity.isFinite && (0...1).contains(opacity)
            && [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) }
    }
}

/// A flat color over everything the layer shows.
struct ColorOverlayEffect: Codable, Equatable, Sendable {
    var enabled: Bool? = nil
    var isEnabled: Bool { enabled ?? true }
    var red: CGFloat = 0
    var green: CGFloat = 0
    var blue: CGFloat = 0
    var opacity: Double = 1
    var color: PaletteColor { PaletteColor(red: red, green: green, blue: blue) }
    var isValid: Bool {
        opacity.isFinite && (0...1).contains(opacity) && [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) }
    }
}

/// A shadow cast inside the layer's own edges, as though it were cut out of what is behind it.
struct InnerShadowEffect: Codable, Equatable, Sendable {
    var enabled: Bool? = nil
    var isEnabled: Bool { enabled ?? true }
    var angle: CGFloat = 90
    var distance: CGFloat = 10
    var blur: CGFloat = 10
    var red: CGFloat = 0
    var green: CGFloat = 0
    var blue: CGFloat = 0
    var opacity: Double = 0.5
    var color: PaletteColor { PaletteColor(red: red, green: green, blue: blue) }
    /// Where the shadow falls, in layer pixels (y grows downward).
    var offset: CGSize {
        let radians = angle * .pi / 180
        return CGSize(width: -cos(radians) * distance, height: sin(radians) * distance)
    }
    var isValid: Bool {
        [angle, distance, blur].allSatisfy(\.isFinite) && (-360...360).contains(angle)
            && (0...5000).contains(distance) && (0...500).contains(blur)
            && opacity.isFinite && (0...1).contains(opacity)
            && [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) }
    }
}

/// A soft glow drawn omnidirectionally around the outside of what the layer shows.
struct OuterGlowEffect: Codable, Equatable, Sendable {
    var enabled: Bool? = nil
    var isEnabled: Bool { enabled ?? true }
    var size: CGFloat = 20
    var red: CGFloat = 1
    var green: CGFloat = 1
    var blue: CGFloat = 1
    var opacity: Double = 0.75
    var color: PaletteColor { PaletteColor(red: red, green: green, blue: blue) }
    var isValid: Bool {
        size.isFinite && (0...500).contains(size)
            && opacity.isFinite && (0...1).contains(opacity)
            && [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) }
    }
}

/// A glow cast inside the layer's own edges, emanating inward from its boundary.
struct InnerGlowEffect: Codable, Equatable, Sendable {
    var enabled: Bool? = nil
    var isEnabled: Bool { enabled ?? true }
    var size: CGFloat = 10
    var red: CGFloat = 1
    var green: CGFloat = 1
    var blue: CGFloat = 1
    var opacity: Double = 0.75
    var color: PaletteColor { PaletteColor(red: red, green: green, blue: blue) }
    var isValid: Bool {
        size.isFinite && (0...500).contains(size)
            && opacity.isFinite && (0...1).contains(opacity)
            && [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) }
    }
}

/// What a layer draws around itself. Kept with the layer, so it follows every edit and can be changed or removed
/// at any time; the pixels themselves are never touched.
struct LayerEffects: Codable, Equatable, Sendable {
    var stroke: StrokeEffect? = nil
    var shadow: ShadowEffect? = nil
    var colorOverlay: ColorOverlayEffect? = nil
    var innerShadow: InnerShadowEffect? = nil
    var outerGlow: OuterGlowEffect? = nil
    var innerGlow: InnerGlowEffect? = nil
    var isEmpty: Bool { stroke == nil && shadow == nil && colorOverlay == nil && innerShadow == nil && outerGlow == nil && innerGlow == nil }
    var isValid: Bool {
        (stroke?.isValid ?? true) && (shadow?.isValid ?? true)
            && (colorOverlay?.isValid ?? true) && (innerShadow?.isValid ?? true)
            && (outerGlow?.isValid ?? true) && (innerGlow?.isValid ?? true)
    }
}

// MARK: - LayerTransform.swift
enum LayerSampling: String, CaseIterable, Codable {
    case nearest = "Nearest"
    case smooth = "Smooth"
    case high = "High quality"
}

struct LayerTransform: Equatable, Codable {
    var origin: CGPoint
    var size: CGSize
    var rotation: CGFloat = 0
    var flipX = false
    var flipY = false
    var sampling: LayerSampling = .high
    var isValid: Bool {
        [origin.x, origin.y, size.width, size.height, rotation].allSatisfy(\.isFinite)
            && (1...300_000).contains(size.width) && (1...300_000).contains(size.height)
            && abs(origin.x) <= 1_000_000 && abs(origin.y) <= 1_000_000
    }
}

// MARK: - LayerAppearance.swift
enum LayerBlendMode: String, Codable, CaseIterable {
    case normal = "Normal"
    case darken = "Darken", multiply = "Multiply", colorBurn = "Color Burn"
    case linearBurn = "Linear Burn"
    case lighten = "Lighten", screen = "Screen", colorDodge = "Color Dodge"
    case linearDodge = "Linear Dodge (Add)"
    case overlay = "Overlay", softLight = "Soft Light", hardLight = "Hard Light"
    case vividLight = "Vivid Light", linearLight = "Linear Light", pinLight = "Pin Light", hardMix = "Hard Mix"
    case difference = "Difference", exclusion = "Exclusion", subtract = "Subtract", divide = "Divide"
    case hue = "Hue", saturation = "Saturation", color = "Color", luminosity = "Luminosity"
}

// MARK: - LayerAdjustment.swift
enum AdjustmentKind: String, Codable, CaseIterable {
    case hsv = "Hue/Saturation", levels = "Levels", curves = "Curves"
    case exposure = "Exposure", gradientMap = "Gradient Map", grain = "Grain", addNoise = "Add Noise"
    case gaussianBlur = "Gaussian Blur", motionBlur = "Motion Blur"
    case invert = "Invert"
    case blackWhite = "Black & White", colorBalance = "Color Balance"
    case progressiveBlur = "Progressive Blur", bloomGlow = "Bloom / Glow", rgbSplit = "RGB Split"
    case vignette = "Vignette", lensEffects = "Lens Effects", tonalContrast = "Tonal Contrast"
    case sharpen = "Sharpen"
}

/// The Filter menu's layer settings (versions 16-20): only which blocks are present is mirrored here, for the
/// version gates; their ranges are not checked.
struct FilterLayerSettings: Codable, Equatable {
    struct Block: Codable, Equatable {}
    var vignette: Block?
    var tonal: Block?
    var lens: Block?
    var sharpen: Block?
}

struct LayerAdjustment: Codable, Equatable {
    var kind: AdjustmentKind
    var hue: Double = 0
    var saturation: Double = 0
    var lightness: Double = 0
    var colorize = false
    var hsvSettings: HueSaturationSettings?
    var resolvedHSV: HueSaturationSettings {
        hsvSettings ?? HueSaturationSettings(hue: hue, saturation: saturation, lightness: lightness, colorize: colorize)
    }
    var levels = LevelsSettings()
    var curves = CurvesSettings()
    var exposureSettings: ExposureSettings?
    var gradientMapSettings: GradientMapSettings?
    var grainSettings: GrainSettings?
    var blackWhiteSettings: BlackWhiteSettings?
    var colorBalanceSettings: ColorBalanceSettings?
    var blurRadius: Double?
    var motionAngle: Double?
    var motionDistance: Double?
    var noiseAmount: Double?
    var noiseGaussian: Bool?
    var noiseMonochromatic: Bool?
    var noiseSeed: UInt32?
    var filterLayerSettings: FilterLayerSettings?
    var exposure: ExposureSettings { exposureSettings ?? ExposureSettings() }
    var gradientMap: GradientMapSettings { gradientMapSettings ?? GradientMapSettings() }
    var grain: GrainSettings { grainSettings ?? GrainSettings() }
    var blackWhite: BlackWhiteSettings { blackWhiteSettings ?? BlackWhiteSettings() }
    var colorBalance: ColorBalanceSettings { colorBalanceSettings ?? ColorBalanceSettings() }
    var gaussianRadius: Double { blurRadius ?? 10 }
    var resolvedMotionAngle: Double { motionAngle ?? 0 }
    var resolvedMotionDistance: Double { motionDistance ?? 10 }
    var resolvedNoiseAmount: Double { noiseAmount ?? 10 }
    var isValid: Bool {
        hue.isFinite && saturation.isFinite && lightness.isFinite && abs(hue) <= 360 && abs(saturation) <= 100 && abs(lightness) <= 100
        && resolvedHSV.adjustments.values.allSatisfy {
            $0.hue.isFinite && abs($0.hue) <= 360 && $0.saturation.isFinite && abs($0.saturation) <= 100
                && $0.lightness.isFinite && abs($0.lightness) <= 100
        }
        && resolvedHSV.bands.values.allSatisfy { $0.handles.allSatisfy { $0.isFinite } }
        && levels.ranges.count == 4 && levels.ranges.allSatisfy { $0 == $0.normalized } && curves.isValid
        && exposure.isValid && gradientMap.isValid && grain.isValid && blackWhite.isValid && colorBalance.isValid
        && gaussianRadius.isFinite && (0.1...250).contains(gaussianRadius)
        && resolvedMotionAngle.isFinite && (-90...90).contains(resolvedMotionAngle)
        && resolvedMotionDistance.isFinite && (1...2000).contains(resolvedMotionDistance)
        && resolvedNoiseAmount.isFinite && (0.1...400).contains(resolvedNoiseAmount)
    }
}

// MARK: - HueSaturation.swift
enum ColorRange: String, CaseIterable, Hashable, Codable {
    case master = "Master", reds = "Reds", yellows = "Yellows", greens = "Greens"
    case cyans = "Cyans", blues = "Blues", magentas = "Magentas"
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
}
struct HueBand: Equatable, Codable {
    var falloffStart: Double
    var rangeStart: Double
    var rangeEnd: Double
    var falloffEnd: Double
    var handles: [Double] { [falloffStart, rangeStart, rangeEnd, falloffEnd] }
}
struct RangeAdjustment: Equatable, Codable {
    var hue: Double = 0
    var saturation: Double = 0
    var lightness: Double = 0
}
struct HueSaturationSettings: Equatable, Codable {
    var range: ColorRange = .master
    var colorize = false
    var invertRange = false
    var adjustments: [ColorRange: RangeAdjustment] = [:]
    var bands: [ColorRange: HueBand] = Dictionary(uniqueKeysWithValues: ColorRange.allCases.map { ($0, $0.defaultBand) })
    init(hue: Double = 0, saturation: Double = 0, lightness: Double = 0, colorize: Bool = false,
         range: ColorRange = .master) {
        self.range = range
        self.colorize = colorize
        adjustments[range] = RangeAdjustment(hue: hue, saturation: saturation, lightness: lightness)
    }
}

// MARK: - Levels.swift
enum LevelsChannel: String, CaseIterable, Codable {
    case rgb = "RGB", red = "Red", green = "Green", blue = "Blue"
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
}
struct LevelsSettings: Equatable, Codable {
    var channel: LevelsChannel = .rgb
    var ranges = Array(repeating: LevelRange(), count: 4)
}

// MARK: - Curves.swift
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
}

// MARK: - ImageAdjustments.swift
struct AdjustmentColor: Codable, Equatable {
    var red: Double
    var green: Double
    var blue: Double
    var isValid: Bool { [red, green, blue].allSatisfy { $0.isFinite && (0...1).contains($0) } }
}
struct ExposureSettings: Codable, Equatable {
    static let exposureRange: ClosedRange<Double> = -20...20
    static let offsetRange: ClosedRange<Double> = -0.5...0.5
    static let gammaRange: ClosedRange<Double> = 0.01...9.99
    var exposure: Double = 0
    var offset: Double = 0
    var gamma: Double = 1
    var isValid: Bool { Self.exposureRange.contains(exposure) && Self.offsetRange.contains(offset) && Self.gammaRange.contains(gamma) }
}
struct GradientMapSettings: Codable, Equatable {
    var shadows = AdjustmentColor(red: 0, green: 0, blue: 0)
    var highlights = AdjustmentColor(red: 1, green: 1, blue: 1)
    var reversed = false
    var isValid: Bool { shadows.isValid && highlights.isValid }
}
struct BlackWhiteSettings: Codable, Equatable {
    static let range: ClosedRange<Double> = -200...300
    var reds: Double = 40
    var yellows: Double = 60
    var greens: Double = 40
    var cyans: Double = 60
    var blues: Double = 20
    var magentas: Double = 80
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
}
struct GrainSettings: Codable, Equatable {
    static let amountRange: ClosedRange<Double> = 0...100
    static let sizeRange: ClosedRange<Double> = 0.5...20
    static let roughnessRange: ClosedRange<Double> = 0...100
    var amount: Double = 25
    var size: Double = 1.5
    var roughness: Double = 50
    var seed: UInt32 = 0
    var isValid: Bool { Self.amountRange.contains(amount) && Self.sizeRange.contains(size) && Self.roughnessRange.contains(roughness) }
}

// MARK: - LayerGroups.swift / LiveLayerMask.swift
enum LayerHierarchy {
    static func validate(_ layers: [ProjectLayerRecord]) throws {
        var byID: [UUID: ProjectLayerRecord] = [:]
        for layer in layers {
            guard byID.updateValue(layer, forKey: layer.id) == nil,
                  layer.isGroup != true || layer.imageFile == nil else { throw ProjectError.invalid("hierarchy: duplicate id or group with image (\(layer.name))") }
        }
        for layer in layers {
            var seen: Set<UUID> = [layer.id]
            var parent = layer.parentID
            while let id = parent {
                guard seen.count <= 64, seen.insert(id).inserted,
                      let node = byID[id], node.isGroup == true else { throw ProjectError.invalid("hierarchy: bad parent of \(layer.name)") }
                parent = node.parentID
            }
            if layer.isGroup == true, seen.count > 64 { throw ProjectError.invalid("hierarchy: too deep") }
        }
    }
}
enum LiveMaskGraph {
    static func validate(_ layers: [ProjectLayerRecord]) throws {
        var records: [UUID: ProjectLayerRecord] = [:]
        for layer in layers {
            guard records.updateValue(layer, forKey: layer.id) == nil else { throw ProjectError.invalid("live mask: duplicate id") }
        }
        for layer in layers {
            var path = Set<UUID>(), current: UUID? = layer.id
            while let id = current {
                guard path.count < 256, path.insert(id).inserted, let record = records[id] else { throw ProjectError.invalid("live mask: cycle") }
                if let source = record.maskSourceID {
                    guard !((record.isGroup ?? false)), records[source] != nil, records[source]?.isGroup != true, records[source]?.adjustment == nil else { throw ProjectError.invalid("live mask: bad source") }
                }
                current = record.maskSourceID
            }
        }
    }
}

// MARK: - LayerMask.swift
func maskIsValid(_ image: CGImage) -> Bool {
    !image.isMask && image.colorSpace?.model == .monochrome
        && image.bitsPerComponent == 8 && image.alphaInfo == .none
}

// MARK: - ProjectStore.validate (verbatim conditions)
func validate(_ manifest: ProjectManifest) throws {
    guard manifest.format == "com.compositor.project" else { throw ProjectError.invalid("format") }
    guard ProjectManifest.supported.contains(manifest.version) else { throw ProjectError.version(manifest.version) }
    guard manifest.colorSpace == "sRGB" else { throw ProjectError.invalid("colorSpace") }
    if let resolution = manifest.resolution {
        guard resolution.isFinite, (1...9600).contains(resolution) else { throw ProjectError.invalid("resolution") }
    }
    guard (1...DocumentLimits.maxSide).contains(manifest.width), (1...DocumentLimits.maxSide).contains(manifest.height),
          manifest.layers.count <= 10_000 else { throw ProjectError.tooLarge("canvas or layer count") }
    for layer in manifest.layers {
        if let text = layer.text {
            guard text.isValid,
                  text.colorRuns == nil || manifest.version >= 10,
                  text.fontRuns == nil || manifest.version >= 11,
                  layer.imageFile != nil, layer.isGroup != true, layer.adjustment == nil else { throw ProjectError.invalid("text on \(layer.name)") }
        }
        if let adjustment = layer.adjustment {
            guard manifest.version >= 7, layer.isGroup != true, layer.imageFile == nil, adjustment.isValid else { throw ProjectError.invalid("adjustment on \(layer.name)") }
            if adjustment.kind == .gaussianBlur || adjustment.kind == .motionBlur || adjustment.kind == .addNoise {
                guard manifest.version >= 9 else { throw ProjectError.invalid("adjustment kind needs v9") }
            }
            let filterLayer = adjustment.filterLayerSettings
            if [.progressiveBlur, .bloomGlow, .rgbSplit].contains(adjustment.kind) || filterLayer != nil {
                guard manifest.version >= 16 else { throw ProjectError.invalid("filter layer needs v16") }
            }
            if [.vignette, .lensEffects, .tonalContrast].contains(adjustment.kind)
                || filterLayer?.vignette != nil || filterLayer?.tonal != nil || filterLayer?.lens != nil {
                guard manifest.version >= 18 else { throw ProjectError.invalid("adjustment kind needs v18") }
            }
            if adjustment.kind == .sharpen || filterLayer?.sharpen != nil {
                guard manifest.version >= 19 else { throw ProjectError.invalid("Sharpen needs v19") }
            }
        }
        guard layer.maskFile == nil || (manifest.version >= (layer.isGroup == true ? 6 : 4)
            && layer.maskFile == "\(layer.id.uuidString).mask.png"),
            layer.maskEnabled == nil || layer.maskFile != nil,
            layer.maskPlacement.map({ $0.isValid && layer.maskFile != nil }) ?? true else { throw ProjectError.invalid("mask fields on \(layer.name)") }
        let opacity = layer.opacity ?? 1
        let blend = layer.blendMode ?? .normal
        guard opacity.isFinite, (0...1).contains(opacity),
              (manifest.version >= 3 || (opacity == 1 && blend == .normal)),
              (layer.isGroup != true || (blend == .normal && (manifest.version >= 8 || opacity == 1))) else { throw ProjectError.invalid("opacity/blend on \(layer.name)") }
    }
    try LayerHierarchy.validate(manifest.layers)
    try LiveMaskGraph.validate(manifest.layers)
    if manifest.version < 5, manifest.layers.contains(where: { $0.maskSourceID != nil }) { throw ProjectError.invalid("maskSourceID needs v5") }
    if manifest.version == 1, manifest.layers.contains(where: { $0.parentID != nil || $0.isGroup == true }) { throw ProjectError.invalid("groups need v2") }
    var ids = Set<UUID>()
    for layer in manifest.layers {
        guard ids.insert(layer.id).inserted, layer.transform.isValid,
              !layer.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              layer.name.utf8.count <= 16_384,
              layer.imageFile == nil || layer.imageFile == "\(layer.id.uuidString).png"
                || (manifest.version >= 13 && layer.imageFile == "\(layer.id.uuidString).exr") else { throw ProjectError.invalid("id/transform/name/imageFile on \(layer.name)") }
    }
    if let id = manifest.activeLayerID, !ids.contains(id) { throw ProjectError.invalid("activeLayerID") }
    let guides = manifest.guides ?? []
    if manifest.version < 8 { guard guides.isEmpty else { throw ProjectError.invalid("guides need v8") } }
}

// MARK: - ProjectStore.readPackage (image checks)
func checkSize(width: Int, height: Int, used: inout Int, _ what: String) throws {
    guard (1...DocumentLimits.maxSide).contains(width), (1...DocumentLimits.maxSide).contains(height),
          width * height <= DocumentLimits.documentPixelBudget - used else {
        throw ProjectError.tooLarge("\(what) \(width)x\(height), budget \(DocumentLimits.documentPixelBudget) used \(used)")
    }
    used += width * height
}

func readPackage(_ url: URL) throws -> (ProjectManifest, Int, Int) {
    let metadata = try Data(contentsOf: url.appendingPathComponent("manifest.json"))
    guard metadata.count <= 4 * 1024 * 1024 else { throw ProjectError.tooLarge("manifest > 4 MiB") }
    struct Header: Decodable { let format: String; let version: Int }
    let header = try JSONDecoder().decode(Header.self, from: metadata)
    guard header.format == "com.compositor.project" else { throw ProjectError.invalid("header format") }
    guard ProjectManifest.supported.contains(header.version) else { throw ProjectError.version(header.version) }
    let manifest = try JSONDecoder().decode(ProjectManifest.self, from: metadata)
    try validate(manifest)
    var pixels = 0, maskPixels = 0
    for layer in manifest.layers {
        for isMask in [false, true] {
            guard let filename = isMask ? layer.maskFile : layer.imageFile else { continue }
            let file = url.appendingPathComponent("images").appendingPathComponent(filename)
            guard let bytes = try? Data(contentsOf: file) else { throw ProjectError.missingImage("\(filename) (\(layer.name))") }
            guard bytes.count <= 512 * 1024 * 1024 else { throw ProjectError.tooLarge(filename) }
            // An HDR layer's EXR from version 13; masks are always PNG.
            let exr = !isMask && filename.hasSuffix(".exr")
            guard !exr || manifest.version >= 13,
                  let source = CGImageSourceCreateWithData(bytes as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
                  CGImageSourceGetType(source) as String? == (exr ? "com.ilm.openexr-image" : UTType.png.identifier),
                  CGImageSourceGetCount(source) == 1,
                  let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
                  let width = properties[kCGImagePropertyPixelWidth] as? Int,
                  let height = properties[kCGImagePropertyPixelHeight] as? Int,
                  // 16-bit layer images from version 12; masks stay 8-bit.
                  case let depth = properties[kCGImagePropertyDepth] as? Int ?? 8,
                  exr || depth <= 8 || (depth == 16 && !isMask && manifest.version >= 12) else {
                throw ProjectError.missingImage("\(filename): not a single PNG (8-bit; 16-bit layers from v12) or OpenEXR (v13)")
            }
            if isMask { try checkSize(width: width, height: height, used: &maskPixels, filename) }
            else { try checkSize(width: width, height: height, used: &pixels, filename) }
            guard let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { throw ProjectError.missingImage("\(filename): decode") }
            if isMask, !maskIsValid(image) { throw ProjectError.invalid("\(filename): mask must be 8-bit gray without alpha") }
        }
    }
    return (manifest, pixels, maskPixels)
}

// MARK: - main
let args = CommandLine.arguments
if args.count >= 2, args[1] == "shapes" {
    // The JSON Compositor itself writes for each adjustment kind the skill uses.
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    var curves = LayerAdjustment(kind: .curves)
    curves.curves.channels[0] = [CurvePoint(x: 0, y: 0), CurvePoint(x: 128, y: 160), CurvePoint(x: 255, y: 255)]
    var bw = LayerAdjustment(kind: .blackWhite)
    bw.blackWhiteSettings = BlackWhiteSettings()
    var grain = LayerAdjustment(kind: .grain)
    grain.grainSettings = GrainSettings(amount: 10, size: 2, roughness: 50, seed: 12345)
    var hsv = LayerAdjustment(kind: .hsv)
    var settings = HueSaturationSettings(hue: 0, saturation: -60, lightness: 10, colorize: false, range: .yellows)
    settings.range = .yellows
    hsv.hsvSettings = settings
    for (name, value) in [("curves", curves), ("blackWhite", bw), ("grain", grain), ("hsv", hsv)] {
        print("=== \(name) ===")
        print(String(data: try! encoder.encode(value), encoding: .utf8)!)
    }
    exit(0)
}
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: CompValidator <project.comp>... | shapes\n".data(using: .utf8)!)
    exit(2)
}
var failed = false
for path in args.dropFirst() {
    let url = URL(fileURLWithPath: path)
    do {
        let (manifest, pixels, maskPixels) = try readPackage(url)
        let adjustments = manifest.layers.filter { $0.adjustment != nil }.count
        let groups = manifest.layers.filter { $0.isGroup == true }.count
        let masks = manifest.layers.filter { $0.maskFile != nil }.count
        for layer in manifest.layers where layer.effects.map({ !$0.isValid }) ?? false {
            throw ProjectError.invalid("\(layer.name): effects out of range (Compositor would load but not draw them)")
        }
        let effects = manifest.layers.filter { $0.effects != nil }.count
        print("OK  \(url.lastPathComponent): v\(manifest.version) \(manifest.width)x\(manifest.height), \(manifest.layers.count) layers (\(adjustments) adjustments, \(groups) folders, \(masks) masks, \(effects) with effects), image px \(pixels), mask px \(maskPixels)")
    } catch let error as DecodingError {
        failed = true
        print("FAIL \(url.lastPathComponent): JSON decode — \(error)")
    } catch {
        failed = true
        print("FAIL \(url.lastPathComponent): \(error)")
    }
}
exit(failed ? 1 : 0)
