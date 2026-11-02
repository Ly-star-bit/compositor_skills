import Foundation

// What the user picks in the inspector, and what each queued photo or project becomes. Everything here maps onto
// retouch_builder.py's command line (see Arguments.swift); nothing retouches by itself.

enum Preset: String, CaseIterable, Identifiable {
    case none, daily, dr5, studio
    var id: String { rawValue }
    var title: String {
        switch self {
        case .none: "自选"
        case .daily: "常用"
        case .dr5: "DR5"
        case .studio: "影棚"
        }
    }
    var detail: String {
        switch self {
        case .none: "只加下面勾选的模块"
        case .daily: "中性灰、DR5 磨皮、观察组：中性灰调光影，再磨一下皮"
        case .dr5: "高低频、DR5 磨皮、3D 骨相、双曲线、眼神光、胶片颗粒、观察组"
        case .studio: "高低频、中性灰、双曲线、观察组，留给手工精修"
        }
    }
    /// dr5 and studio build a project from scratch, so they need a photo, not an existing project.
    var needsPhoto: Bool { self == .dr5 || self == .studio }
    /// The modules the preset already adds (retouch_builder.py's PRESETS), shown ticked and fixed.
    var includes: Set<String> {
        switch self {
        case .none: []
        case .daily: ["delicious-skin", "neutral-gray", "inspection"]
        case .dr5: ["freq-sep", "delicious-skin", "sculpt", "dual-curves", "eyes", "grain", "inspection"]
        case .studio: ["freq-sep", "neutral-gray", "dual-curves", "inspection"]
        }
    }
}

struct Module: Identifiable, Hashable {
    let flag: String      // retouch_builder.py's option, without the dashes
    let title: String
    let symbol: String
    let note: String
    var id: String { flag }
    var needsLandmarks = false
}

enum ModuleGroup: String, CaseIterable, Identifiable {
    case skin, light, features
    var id: String { rawValue }
    var title: String {
        switch self {
        case .skin: "皮肤"
        case .light: "光影与工具"
        case .features: "五官"
        }
    }
    var modules: [Module] {
        switch self {
        case .skin: [
            Module(flag: "delicious-skin", title: "DR5 磨皮", symbol: "circle.hexagongrid", note: "先祛痘，再匀肤；保留毛孔和五官立体"),
            Module(flag: "freq-sep", title: "高低频", symbol: "square.split.2x1", note: "按脸宽分开，留给手工修的两层"),
        ]
        case .light: [
            Module(flag: "neutral-gray", title: "中性灰", symbol: "circle.grid.2x2", note: "50% 灰柔光层，手工加深减淡"),
            Module(flag: "dual-curves", title: "双曲线", symbol: "chart.xyaxis.line", note: "提亮、压暗两层曲线，黑蒙版"),
            Module(flag: "inspection", title: "观察组", symbol: "eye.trianglebadge.exclamationmark", note: "黑白看明暗、颜色看泛红偏色，默认关闭"),
        ]
        case .features: [
            Module(flag: "sculpt", title: "3D 骨相", symbol: "cube", note: "Tonal Contrast 实时层，预涂五官的面"),
            Module(flag: "eyes", title: "眼神光", symbol: "eye", note: "Sharpen 实时层，预涂虹膜"),
        ]
        }
    }
}

/// How the DR5 smoothing layer is masked (the script's --delicious-skin auto|manual): on the skin it found, or
/// black, to be brushed on by hand as in DR5's manual smoothing.
enum SmoothingMask: String, CaseIterable, Identifiable {
    case auto, manual
    var id: String { rawValue }
    var title: String {
        switch self {
        case .auto: "自动蒙版"
        case .manual: "手动刷出"
        }
    }
}

/// How hard the DR5 smoothing works (the script's --skin-strength): its opacity, 53% / 88% / 100%, and how readily
/// it heals spots first. Apart from the 强度 slider, which is for the glow and the color grade.
enum SkinStrength: String, CaseIterable, Identifiable {
    case light, medium, strong
    var id: String { rawValue }
    var title: String {
        switch self {
        case .light: "轻"
        case .medium: "中"
        case .strong: "重"
        }
    }
    var value: Double {
        switch self {
        case .light: 0.6
        case .medium: 1.0
        case .strong: 1.15
        }
    }
}

/// How much fine skin texture the smoothing keeps (the script's --texture): natural keeps about all of the photo's,
/// soft about as much as DR5's inverted high pass, smooth less, for a polished look.
enum SkinTexture: String, CaseIterable, Identifiable {
    case natural, soft, smooth
    var id: String { rawValue }
    var title: String {
        switch self {
        case .natural: "自然"
        case .soft: "柔和"
        case .smooth: "光滑"
        }
    }
}

enum Look: String, CaseIterable, Identifiable {
    case none, airy, film, coolFair = "cool-fair", creamy, hk, bw
    var id: String { rawValue }
    var title: String {
        switch self {
        case .none: "不调色"
        case .airy: "日系清透"
        case .film: "胶片暖调"
        case .coolFair: "冷白皮"
        case .creamy: "奶油肌"
        case .hk: "港风"
        case .bw: "黑白"
        }
    }
    /// Two colors that suggest the grade, for its swatch (sRGB 0-1).
    var swatch: [(Double, Double, Double)] {
        switch self {
        case .none: [(0.86, 0.86, 0.88), (0.70, 0.70, 0.74)]
        case .airy: [(0.80, 0.92, 0.96), (0.62, 0.80, 0.86)]
        case .film: [(0.98, 0.80, 0.55), (0.36, 0.52, 0.52)]
        case .coolFair: [(0.97, 0.93, 0.96), (0.74, 0.82, 0.95)]
        case .creamy: [(1.00, 0.93, 0.82), (0.93, 0.78, 0.66)]
        case .hk: [(0.90, 0.30, 0.24), (0.12, 0.42, 0.40)]
        case .bw: [(0.95, 0.95, 0.95), (0.15, 0.15, 0.15)]
        }
    }
}

enum GlowStyle: String, CaseIterable, Identifiable {
    case none, mist, halation, dream
    var id: String { rawValue }
    var title: String {
        switch self {
        case .none: "无"
        case .mist: "黑柔"
        case .halation: "胶片光晕"
        case .dream: "梦幻柔焦"
        }
    }
}

enum GlowColorMode: String, CaseIterable, Identifiable {
    case natural, single, twoTone
    var id: String { rawValue }
    var title: String {
        switch self {
        case .natural: "原色"
        case .single: "单色"
        case .twoTone: "双色"
        }
    }
}

/// An sRGB color as the glow's #RRGGBB.
struct RGB: Equatable, Hashable {
    var red: Double, green: Double, blue: Double
    var hex: String {
        func byte(_ v: Double) -> Int { Int((min(1, max(0, v)) * 255).rounded()) }
        return String(format: "#%02X%02X%02X", byte(red), byte(green), byte(blue))
    }
    init(red: Double, green: Double, blue: Double) {
        self.red = red; self.green = green; self.blue = blue
    }
    /// From "#RRGGBB" (as `hex` writes it), or nil for anything else.
    init?(hex: String) {
        let digits = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        guard digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
        self.init(red: Double((value >> 16) & 0xFF) / 255, green: Double((value >> 8) & 0xFF) / 255, blue: Double(value & 0xFF) / 255)
    }
    /// The skill's named glow colors, offered as one-click swatches.
    static let named: [(String, RGB)] = [
        ("暖金", RGB(red: 1, green: 0xD0 / 255.0, blue: 0x8A / 255.0)),
        ("胶片橙红", RGB(red: 1, green: 0x99 / 255.0, blue: 0x4E / 255.0)),
        ("玫瑰粉", RGB(red: 1, green: 0x8F / 255.0, blue: 0xB8 / 255.0)),
        ("薰衣草紫", RGB(red: 0xB8 / 255.0, green: 0xA0 / 255.0, blue: 1)),
        ("冰蓝", RGB(red: 0x9C / 255.0, green: 0xCB / 255.0, blue: 1)),
        ("青", RGB(red: 0x6F / 255.0, green: 0xE3 / 255.0, blue: 0xD6 / 255.0)),
    ]
}

enum OutputPlace: Equatable {
    case besidePhoto
    case folder(URL)
}

/// A preset is where the switches start: picking one turns its modules on, and any switch can then be changed. The
/// script is still asked for the preset, with the modules switched on beside it and those switched off left out
/// (`--without`). The app remembers these across launches (see `save` / `load`).
struct RetouchSettings: Equatable {
    var preset: Preset = .daily
    var modules: Set<String> = []    // switched on beyond the preset (grain included)
    var removed: Set<String> = []    // the preset's own, switched off
    var smoothingMask: SmoothingMask = .auto
    var skinStrength: SkinStrength = .medium
    var strength: Double = 1.0
    var texture: SkinTexture = .natural
    var look: Look = .none
    var glow: GlowStyle = .none
    var glowColorMode: GlowColorMode = .natural
    var glowColor = RGB.named[0].1
    var glowOuterColor = RGB.named[2].1
    var render = true
    var checks = true
    var output: OutputPlace = .besidePhoto

    /// Starts over from a preset: its modules on, nothing else.
    mutating func choose(_ preset: Preset) {
        self.preset = preset
        modules = []
        removed = []
    }

    func isOn(_ flag: String) -> Bool {
        (preset.includes.contains(flag) && !removed.contains(flag)) || modules.contains(flag)
    }

    mutating func set(_ flag: String, _ on: Bool) {
        if preset.includes.contains(flag) {
            if on { removed.remove(flag) } else { removed.insert(flag) }
        } else {
            if on { modules.insert(flag) } else { modules.remove(flag) }
        }
    }

    /// Every module that will run.
    var effectiveModules: Set<String> { preset.includes.subtracting(removed).union(modules) }

    /// Changed from the preset as picked.
    var isCustomized: Bool { !modules.isEmpty || !removed.isEmpty }

    /// Whether running these settings changes anything at all.
    var doesSomething: Bool {
        !effectiveModules.isEmpty || look != .none || glow != .none
    }
}

// MARK: remembered across launches

extension RetouchSettings {  // an extension, so the struct keeps its memberwise init
    static let defaultsKey = "settings"

    /// The settings as a property list (strings, numbers, arrays), as UserDefaults keeps it.
    var asDictionary: [String: Any] {
        var dictionary: [String: Any] = [
            "preset": preset.rawValue, "modules": modules.sorted(), "removed": removed.sorted(),
            "smoothingMask": smoothingMask.rawValue, "skinStrength": skinStrength.rawValue, "strength": strength,
            "texture": texture.rawValue, "look": look.rawValue, "glow": glow.rawValue,
            "glowColorMode": glowColorMode.rawValue, "glowColor": glowColor.hex, "glowOuterColor": glowOuterColor.hex,
            "render": render, "checks": checks,
        ]
        if case .folder(let url) = output { dictionary["outputFolder"] = url.path }
        return dictionary
    }

    /// Settings read back from `asDictionary`, forgiving: whatever is missing or unknown (a preset or a module of
    /// another version, a value out of range, an output folder that is gone) stays at its default.
    init(dictionary: [String: Any], folderExists: (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) }) {
        self.init()
        if let raw = dictionary["preset"] as? String, let value = Preset(rawValue: raw) { preset = value }
        let known = Set(Arguments.moduleOrder)
        if let list = dictionary["modules"] as? [String] { modules = Set(list).intersection(known).subtracting(preset.includes) }
        if let list = dictionary["removed"] as? [String] { removed = Set(list).intersection(preset.includes) }
        if let raw = dictionary["smoothingMask"] as? String, let value = SmoothingMask(rawValue: raw) { smoothingMask = value }
        if let raw = dictionary["skinStrength"] as? String, let value = SkinStrength(rawValue: raw) { skinStrength = value }
        if let value = dictionary["strength"] as? Double, (0.1...2).contains(value) { strength = value }
        if let raw = dictionary["texture"] as? String, let value = SkinTexture(rawValue: raw) { texture = value }
        if let raw = dictionary["look"] as? String, let value = Look(rawValue: raw) { look = value }
        if let raw = dictionary["glow"] as? String, let value = GlowStyle(rawValue: raw) { glow = value }
        if let raw = dictionary["glowColorMode"] as? String, let value = GlowColorMode(rawValue: raw) { glowColorMode = value }
        if let hex = dictionary["glowColor"] as? String, let color = RGB(hex: hex) { glowColor = color }
        if let hex = dictionary["glowOuterColor"] as? String, let color = RGB(hex: hex) { glowOuterColor = color }
        if let value = dictionary["render"] as? Bool { render = value }
        if let value = dictionary["checks"] as? Bool { checks = value }
        if let path = dictionary["outputFolder"] as? String {
            let url = URL(fileURLWithPath: path, isDirectory: true)
            if folderExists(url) { output = .folder(url) }
        }
    }

    func save(to defaults: UserDefaults = .standard) {
        defaults.set(asDictionary, forKey: Self.defaultsKey)
    }

    static func load(from defaults: UserDefaults = .standard) -> RetouchSettings {
        defaults.dictionary(forKey: defaultsKey).map { RetouchSettings(dictionary: $0) } ?? RetouchSettings()
    }
}

enum JobStatus: Equatable {
    case waiting, running, done, failed(String), cancelled
    var isFinished: Bool {
        switch self {
        case .done, .failed, .cancelled: true
        default: false
        }
    }
}

/// One photo (a new project is made from it) or one existing .comp project (layers are added to it).
struct RetouchJob: Identifiable, Equatable {
    let id = UUID()
    let source: URL
    var status: JobStatus = .waiting
    var project: URL? = nil          // the .comp written or added to
    var render: URL? = nil           // the flattened preview, when rendered
    var checks: [URL] = []           // check images
    var step = ""                    // what the script is doing now
    var fraction: Double? = nil      // how far along it is, while running
    var log = ""                     // everything it printed, each line after the seconds since the start
    var lastError: String? = nil     // the script's own reason, when it stopped on one
    var warnings: [String] = []
    var seconds: Double? = nil
    var finished: Date? = nil        // when it last ended

    var refresh = false              // 重算: its automatic layers recomputed in place (--refresh), nothing added
    var retried = false              // run once more after Compositor saved the project mid-run (never twice)

    var isProject: Bool { source.pathExtension.lowercased() == "comp" }
    var name: String { source.deletingPathExtension().lastPathComponent }

    static let photoExtensions: Set<String> = ["jpg", "jpeg", "png", "tif", "tiff", "heic", "heif"]
    static func accepts(_ url: URL) -> Bool {
        let ext = url.pathExtension.lowercased()
        return ext == "comp" || photoExtensions.contains(ext)
    }
}
