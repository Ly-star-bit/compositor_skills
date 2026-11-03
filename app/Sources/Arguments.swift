import Foundation
import Synchronization

// UI state to retouch_builder.py's command line, and the script's output back to what the UI shows. Pure functions,
// checked by Tests/LogicTests.swift.

enum Arguments {
    /// `Name.comp` next to the photo (or in `folder`), or `Name 2.comp`, `Name 3.comp`... when that is taken: an
    /// existing project is never replaced (the script would refuse without --force, which the app never passes).
    /// For PhotoCraft the same with `.pcraft`.
    static func newProject(for photo: URL, in folder: URL?, documentExtension: String = "comp",
                           exists: (URL) -> Bool) -> URL {
        let directory = folder ?? photo.deletingLastPathComponent()
        let name = photo.deletingPathExtension().lastPathComponent
        var candidate = directory.appendingPathComponent("\(name).\(documentExtension)")
        var number = 2
        // The check images and render sit beside the project under the same name, so those count as taken too.
        while exists(candidate) || exists(checksFolder(of: candidate)) || exists(renderImage(of: candidate)) {
            candidate = directory.appendingPathComponent("\(name) \(number).\(documentExtension)")
            number += 1
        }
        return candidate
    }

    static func renderImage(of project: URL) -> URL {
        project.deletingPathExtension().appendingPathExtension("render.jpg")
    }

    static func checksFolder(of project: URL) -> URL {
        project.deletingPathExtension().appendingPathExtension("checks")
    }

    /// Why these settings can't run on this job, or nil when they can. 重算 needs no settings, only a project.
    static func problem(with settings: RetouchSettings, for job: RetouchJob) -> String? {
        if job.refresh { return job.isProject ? nil : "只有工程能重算" }
        return settings.doesSomething ? nil : "还没有选择任何效果"
    }

    /// The layers 重算 recomputes, as retouch_builder.py's REFRESHED: how their names begin, what else the names
    /// say, their blend mode. Snapshots of what was below them, which cover manual work done under them since.
    static let refreshed: [(begins: String, says: String, blend: String)] = [
        ("🔥", "保边磨皮", "Normal"), ("👁️", "眼神光", "Normal"),
    ]

    private struct Layer: Decodable { let name: String; let imageFile: String?; let blendMode: String?; let isGroup: Bool? }
    private struct Manifest: Decodable { let layers: [Layer] }

    /// The pixel layers of a manifest the script would recompute or re-make: the name begins with the emoji (the
    /// rest is the user's to change) and the blend mode is the module's.
    private static func snapshotLayers(in manifest: Data) -> [Layer] {
        guard let manifest = try? JSONDecoder().decode(Manifest.self, from: manifest) else { return [] }
        return manifest.layers.filter { layer in
            layer.imageFile != nil && layer.isGroup != true && refreshed.contains {
                layer.name.hasPrefix($0.begins) && (layer.blendMode ?? "Normal") == $0.blend
            }
        }
    }

    /// Whether a project's manifest has a layer 重算 would recompute.
    static func refreshable(manifest: Data) -> Bool { !snapshotLayers(in: manifest).isEmpty }

    /// The ways a project's smoothing layers are masked, from their names: ✋ after the 🔥 (or the words earlier
    /// versions put at the end) means brushed on by hand, any other 🔥 layer the skin mask. Empty without one.
    static func smoothingKinds(manifest: Data) -> Set<SmoothingMask> {
        Set(snapshotLayers(in: manifest).filter { $0.name.hasPrefix("🔥") }.map {
            $0.name.hasPrefix("🔥✋") || $0.name.contains("黑蒙版 · 画笔涂白处磨皮") ? SmoothingMask.manual : .auto
        })
    }

    /// refreshable(manifest:) for a project on disk, read again only when its manifest changes (views ask often).
    static func canRefresh(_ project: URL) -> Bool { facts(of: project).refreshable }

    /// smoothingKinds(manifest:) for a project on disk, cached the same way.
    static func smoothingKinds(of project: URL) -> Set<SmoothingMask> { facts(of: project).kinds }

    private static func facts(of project: URL) -> (refreshable: Bool, kinds: Set<SmoothingMask>) {
        let manifest = project.appendingPathComponent("manifest.json")
        let changed = (try? manifest.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        return factsCache.withLock { cache in
            if let known = cache[project.path], known.changed == changed { return (known.refreshable, known.kinds) }
            let data = try? Data(contentsOf: manifest)
            let facts = (data.map(refreshable(manifest:)) ?? false, data.map(smoothingKinds(manifest:)) ?? [])
            cache[project.path] = (changed, facts.0, facts.1)
            return facts
        }
    }
    private static let factsCache = Mutex<[String: (changed: Date?, refreshable: Bool, kinds: Set<SmoothingMask>)]>([:])

    /// Every module switch in the inspector's order, grain last.
    static let moduleOrder: [String] = ModuleGroup.allCases.flatMap { $0.modules.map(\.flag) } + ["grain"]

    /// What the settings will do, in a line: "DR5（已调整）· 7 项 · 自动蒙版 · 高低频 8px · 胶片暖调 · 黑柔".
    static func summary(_ settings: RetouchSettings) -> String {
        var parts = [settings.preset == .none ? "自选" : settings.preset.title + (settings.isCustomized ? "（已调整）" : "")]
        parts.append("\(settings.effectiveModules.count) 项")
        if settings.effectiveModules.contains("delicious-skin") {
            if settings.smoothingMask == .auto { parts.append("自动蒙版") }
            if settings.skinStrength != .medium { parts.append("磨皮" + settings.skinStrength.title) }
        }
        if settings.effectiveModules.contains("freq-sep") && !settings.freqSepAuto {
            parts.append("高低频 " + radiusText(settings.freqSepRadius))
        }
        if settings.look != .none { parts.append(settings.look.title) }
        if settings.glow != .none { parts.append(settings.glow.title) }
        return parts.joined(separator: " · ")
    }

    /// "8px", "6.5px".
    static func radiusText(_ radius: Double) -> String { String(format: "%gpx", radius) }

    static func glowSpec(_ settings: RetouchSettings) -> String? {
        switch settings.glow {
        case .none: return nil
        case .dream: return "dream"
        case .mist, .halation:
            let style = settings.glow.rawValue
            switch settings.glowColorMode {
            case .natural: return settings.glow == .halation ? "halation:natural" : "mist"
            case .single: return "\(style):\(settings.glowColor.hex)"
            case .twoTone: return "\(style):\(settings.glowColor.hex),\(settings.glowOuterColor.hex)"
            }
        }
    }

    /// The script's arguments for one job. `input` is the photo to start from (a converted copy for HEIC).
    /// `inCompositor`: the project is open in Compositor, which shows the result itself, so no preview is rendered.
    static func command(for job: RetouchJob, input: URL, project: URL, settings: RetouchSettings,
                        inCompositor: Bool = false) -> [String] {
        let document = job.target.documentOption  // --comp, or --pcraft for PhotoCraft's script
        if job.refresh {  // the layers' own settings stand, written in their names
            return [document, project.path, "--refresh"] + (settings.render && !inCompositor ? ["--render"] : [])
                + (settings.checks ? [] : ["--no-checks"]) + ["--progress"]
        }
        var args: [String] = job.isProject ? [document, project.path] : ["--init", input.path, document, project.path]
        // DR5 and studio are new-project presets in the script; on an existing project their modules go one by one
        let usePreset = settings.preset != .none && !(job.isProject && settings.preset.needsPhoto)
        if usePreset { args += ["--preset", settings.preset.rawValue] }
        var named = usePreset ? settings.modules.subtracting(settings.preset.includes) : settings.effectiveModules
        // The smoothing is named with its mask even when the preset includes it, so the script never falls back on
        // its own default (it takes the union; `--delicious-skin auto` on top of --preset dr5 makes one layer).
        let smoothing = settings.effectiveModules.contains("delicious-skin")
        if smoothing { named.insert("delicious-skin") }
        args += moduleOrder.filter(named.contains).flatMap {
            $0 == "delicious-skin" ? ["--delicious-skin", settings.smoothingMask.rawValue] : ["--\($0)"]
        }
        // The radius goes with every frequency separation: on a pair no one has worked on yet, another one makes it
        // again (back to 自动 too); a pair worked on keeps its own.
        if settings.effectiveModules.contains("freq-sep") {
            args += ["--blur-radius", settings.freqSepAuto ? "auto" : String(format: "%g", settings.freqSepRadius)]
        }
        if usePreset {
            args += moduleOrder.filter { settings.removed.contains($0) && settings.preset.includes.contains($0) }
                .flatMap { ["--without", $0] }
        }
        if abs(settings.strength - 1) > 0.001 {
            args += ["--strength", String(format: "%.2f", min(2, max(0.1, settings.strength)))]
        }
        // The smoothing has its own strength, so the slider (glow, color grade) never leaks into the skin.
        if settings.effectiveModules.contains("delicious-skin")
            && (settings.skinStrength != .medium || abs(settings.strength - 1) > 0.001) {
            args += ["--skin-strength", String(format: "%.2f", settings.skinStrength.value)]
        }
        if settings.texture != .natural && settings.effectiveModules.contains("delicious-skin") {
            args += ["--texture", settings.texture.rawValue]
        }
        if settings.look != .none { args += ["--look", settings.look.rawValue] }
        if let glow = glowSpec(settings) { args += ["--glow", glow] }
        if settings.render && !inCompositor { args.append("--render") }
        if !settings.checks { args.append("--no-checks") }
        args.append("--progress")
        return args
    }

    enum LineKind: Equatable { case step, ok, warning, skip, success, error, other }

    /// One line of the script's output: its kind and the text to show (the bracketed tag left off).
    static func parse(_ line: String) -> (kind: LineKind, text: String) {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        for (tag, kind) in [("[STEP]", LineKind.step), ("[OK]", .ok), ("[WARN]", .warning), ("[SKIP]", .skip),
                            ("[SUCCESS]", .success), ("[Error]", .error)] where trimmed.hasPrefix(tag) {
            return (kind, String(trimmed.dropFirst(tag.count)).trimmingCharacters(in: .whitespaces))
        }
        return (.other, trimmed)
    }

    /// A --progress line ("2/5 heal", "render", "checks") as what to show and how far along the photo is: the
    /// modules take most of the time, the render and the check images the rest.
    static func step(_ text: String) -> (title: String, fraction: Double)? {
        if text.hasPrefix("downloading photocraft-cli") { return ("首次下载 PhotoCraft 引擎（约 107 MB）", 0.02) }
        switch text {
        case "render": return ("渲染预览", 0.86)
        case "checks": return ("生成检查图", 0.94)
        default:
            let parts = text.split(separator: " ", maxSplits: 1)
            let counts = parts.first?.split(separator: "/").compactMap { Int($0) } ?? []
            guard parts.count == 2, counts.count == 2, counts[1] > 0 else { return nil }
            return (title(ofModule: String(parts[1])), 0.82 * Double(counts[0] - 1) / Double(counts[1]))
        }
    }

    /// The name the inspector gives a module, from the script's key for it ("delicious_skin").
    static func title(ofModule key: String) -> String {
        let flag = key.replacingOccurrences(of: "_", with: "-")
        if let module = ModuleGroup.allCases.lazy.flatMap(\.modules).first(where: { $0.flag == flag }) { return module.title }
        switch key {
        case "look": return "调色"
        case "glow": return "辉光"
        case "grain": return "胶片颗粒"
        case "refresh": return "重算自动层"
        case "builder": return "计算图层"            // PhotoCraft, a new document: the layers first,
        case "photocraft": return "搭建 PhotoCraft 文档"  // then the document from them
        default: return key
        }
    }

    /// Why a photo failed, for the window: the common reasons in words, anything else as the script said it (the
    /// full message is in the log either way).
    static func failure(_ error: String?, status: Int32) -> String {
        guard let error else { return "修图脚本意外退出（代码 \(status)），详见运行记录" }
        if error.hasPrefix("cannot read") && error.contains("manifest.json") { return "工程文件损坏，读不了它的 manifest.json" }
        if error.hasPrefix("cannot read") { return "读不了这个文件：它不是照片，或者格式不支持（支持 JPEG、PNG、TIFF、HEIC）" }
        if error.hasPrefix("input image not found") { return "找不到这张照片，可能被移动或删除了" }
        if error.hasPrefix("no Compositor project") { return "这不是 Compositor 工程（里面没有 manifest.json）" }
        if error.contains("is larger than Compositor's") { return "照片太大，超过了 Compositor 每边的像素上限" }
        if error.contains("already exists") { return "同名工程已经存在" }
        if error.hasPrefix("Compositor would reject") { return "生成的工程 Compositor 会拒绝打开，所以什么都没改（详见运行记录）" }
        if error.hasPrefix("project changed on disk") { return "运行期间 Compositor 又保存了这个工程，这次没有改动它；请再点一次精修" }
        // photocraft-retouch's
        if error.contains("exists: add --force") { return "同名文档已经存在" }
        if PhotoCraftMessages.savedMeanwhile(error) { return "运行期间 PhotoCraft 又保存了这个文档，这次没有改动它；请再点一次精修" }
        if error.contains("is a folder bundle") { return "这是 PhotoCraft 的自动恢复文件夹：请先在 PhotoCraft 里另存为 .pcraft 文件" }
        if error.hasPrefix("no document") { return "找不到这个 PhotoCraft 文档，可能被移动或删除了" }
        if error.hasPrefix("could not download photocraft-cli") || error.hasPrefix("Gatekeeper did not accept") {
            return "PhotoCraft 引擎没能下载或校验，请检查网络后再试（详见运行记录）"
        }
        if error.hasPrefix("retouch_builder.py not found") { return "找不到 Compositor 版的修图脚本 retouch_builder.py（PhotoCraft 版要用它计算图层）" }
        return error
    }

    /// photocraft-retouch's messages the app acts on.
    enum PhotoCraftMessages {
        /// The document was saved in PhotoCraft while the script ran, so it wrote nothing: run once more.
        static func savedMeanwhile(_ error: String) -> Bool { error.contains("was saved (in PhotoCraft?) while this ran") }
    }

    /// [OK] lines worth showing while they happen: the slow first-run downloads and builds. The rest stay in the log.
    static func notice(_ text: String) -> String? {
        if text.hasPrefix("Downloading the hair and skin segmentation model") { return "首次下载人像分割模型（16 MB）" }
        if text.hasPrefix("Building the Apple Vision face helper") { return "首次准备人脸识别组件" }
        if text.hasPrefix("photocraft-cli") && text.contains("cached at") { return "PhotoCraft 引擎已下载并校验" }
        return nil
    }
}
