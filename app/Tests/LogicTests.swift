// The app's pure parts: settings to retouch_builder.py's command line, project naming, the script's output back to
// what the window shows. Fast and offline; built and run by Tests/run.sh with the app's sources (all but App.swift).
import Foundation

@main
enum LogicTests {
    nonisolated(unsafe) static var failures = 0

    static func expect<T: Equatable>(_ actual: T, _ expected: T, _ what: String) {
        if actual == expected { print("  ok    \(what)") } else {
            print("  FAIL  \(what)\n        got      \(actual)\n        expected \(expected)")
            failures += 1
        }
    }

    static func main() {
        let photo = URL(fileURLWithPath: "/shoot/Anna.jpg")
        let project = URL(fileURLWithPath: "/shoot/Anna.comp")
        let job = RetouchJob(source: photo)
        let existing = RetouchJob(source: project)
        let tail = ["--render", "--progress"]

        print("== command line")
        var settings = RetouchSettings()
        expect(Arguments.command(for: job, input: photo, project: project, settings: settings),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "daily"] + tail, "the defaults: 常用, rendered")
        expect(Arguments.command(for: existing, input: project, project: project, settings: settings),
               ["--comp", "/shoot/Anna.comp", "--preset", "daily"] + tail, "常用 goes on a project as the preset itself")
        settings.preset = .none
        settings.modules = ["eyes", "delicious-skin", "neutral-gray", "freq-sep"]
        expect(Arguments.command(for: job, input: photo, project: project, settings: settings),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--delicious-skin", "--freq-sep", "--neutral-gray", "--eyes"] + tail,
               "modules in the inspector's order")
        settings = RetouchSettings(preset: .studio, strength: 0.7, look: .coolFair, render: false, checks: false)
        expect(Arguments.command(for: job, input: photo, project: project, settings: settings),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "studio", "--strength", "0.70",
                "--look", "cool-fair", "--no-checks", "--progress"],
               "strength, look, no render, no checks")
        settings.strength = 1.0000001
        expect(Arguments.command(for: job, input: photo, project: project, settings: settings).contains("--strength"), false,
               "strength 1 is left out")
        var textured = RetouchSettings(texture: .soft)
        expect(Arguments.command(for: job, input: photo, project: project, settings: textured),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "daily", "--texture", "soft"] + tail,
               "a softer skin texture")
        textured.set("delicious-skin", false)
        expect(Arguments.command(for: job, input: photo, project: project, settings: textured).contains("--texture"), false,
               "no texture without the smoothing")
        expect(Arguments.command(for: job, input: photo, project: project, settings: RetouchSettings(texture: .natural))
               .contains("--texture"), false, "natural is the script's default, left out")

        print("== 手动刷出 (DR5's manual smoothing)")
        var manual = RetouchSettings(smoothingMask: .manual)
        expect(Arguments.command(for: job, input: photo, project: project, settings: manual),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "daily", "--delicious-skin", "manual"] + tail,
               "常用 on a photo: the preset plus the smoothing named with its value")
        manual.preset = .dr5
        expect(Arguments.command(for: existing, input: project, project: project, settings: manual),
               ["--comp", "/shoot/Anna.comp", "--delicious-skin", "manual", "--freq-sep", "--dual-curves", "--inspection",
                "--sculpt", "--eyes", "--grain"] + tail,
               "DR5 on a project: module by module, the smoothing with its value")
        manual.preset = .none
        manual.modules = ["delicious-skin", "neutral-gray"]
        expect(Arguments.command(for: existing, input: project, project: project, settings: manual),
               ["--comp", "/shoot/Anna.comp", "--delicious-skin", "manual", "--neutral-gray"] + tail, "自选: the same")
        expect(Arguments.summary(manual), "自选 · 2 项 · 手动刷出", "the summary says so")
        manual.set("delicious-skin", false)
        expect(Arguments.command(for: existing, input: project, project: project, settings: manual),
               ["--comp", "/shoot/Anna.comp", "--neutral-gray"] + tail, "...and nothing about it when the smoothing is off")
        expect(Arguments.summary(manual), "自选 · 1 项", "nor in the summary")
        var studioManual = RetouchSettings(preset: .studio, smoothingMask: .manual)
        expect(Arguments.command(for: job, input: photo, project: project, settings: studioManual),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "studio"] + tail,
               "a preset without the smoothing: the mask choice changes nothing")
        studioManual.set("delicious-skin", true)
        expect(Arguments.command(for: job, input: photo, project: project, settings: studioManual),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "studio", "--delicious-skin", "manual"] + tail,
               "...until the smoothing is switched on beside it")

        expect(Arguments.command(for: existing, input: project, project: project, settings: RetouchSettings(preset: .none, modules: ["freq-sep", "delicious-skin"])),
               ["--comp", "/shoot/Anna.comp", "--delicious-skin", "--freq-sep"] + tail, "an existing project: --comp only")
        var refresh = existing
        refresh.refresh = true
        expect(Arguments.command(for: refresh, input: project, project: project,
                                 settings: RetouchSettings(preset: .dr5, strength: 0.6, look: .film)),
               ["--comp", "/shoot/Anna.comp", "--refresh"] + tail, "重算: --refresh alone, the layers keep their own settings")
        expect(Arguments.command(for: refresh, input: project, project: project, settings: RetouchSettings(checks: false),
                                 inCompositor: true),
               ["--comp", "/shoot/Anna.comp", "--refresh", "--no-checks", "--progress"], "重算 in Compositor: no render")
        print("== skin strength")
        expect(Arguments.command(for: job, input: photo, project: project, settings: RetouchSettings(skinStrength: .light)),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "daily", "--skin-strength", "0.60"] + tail,
               "轻: the smoothing's own strength")
        expect(Arguments.command(for: job, input: photo, project: project, settings: RetouchSettings(strength: 0.8)),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "daily", "--strength", "0.80", "--skin-strength", "1.00"] + tail,
               "the slider pins the smoothing at 中, so it only moves the glow and the grade")
        expect(Arguments.command(for: job, input: photo, project: project, settings: RetouchSettings(preset: .studio, skinStrength: .strong)),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "studio"] + tail,
               "no smoothing, no skin strength")
        expect(Arguments.summary(RetouchSettings(skinStrength: .strong)), "常用 · 3 项 · 磨皮重", "the summary names it")
        expect(SkinStrength.allCases.map(\.value), [0.6, 1.0, 1.15], "轻 中 重 as the script's --skin-strength")

        print("== switches")
        var custom = RetouchSettings()
        expect(custom.isOn("neutral-gray") && !custom.isOn("dual-curves"), true, "常用 starts with its own modules on")
        custom.set("inspection", false)
        custom.set("dual-curves", true)
        expect(custom.isCustomized && !custom.isOn("inspection") && custom.isOn("dual-curves"), true, "any switch can change")
        expect(Arguments.command(for: job, input: photo, project: project, settings: custom),
               ["--init", "/shoot/Anna.jpg", "--comp", "/shoot/Anna.comp", "--preset", "daily", "--dual-curves", "--without", "inspection"] + tail,
               "changes ride on the preset: added by name, the preset's own left out with --without")
        expect(Arguments.summary(custom), "常用（已调整） · 3 项", "the summary says it was changed")
        custom.set("inspection", true)
        custom.set("dual-curves", false)
        expect(custom.isCustomized, false, "switching back is the preset again")
        custom.set("neutral-gray", false)
        custom.choose(.daily)
        expect(custom.isCustomized, false, "picking a preset starts over")
        custom.choose(.none)
        expect(custom.doesSomething, false, "自选 starts with nothing")
        custom.set("neutral-gray", true)
        expect(Arguments.command(for: existing, input: project, project: project, settings: custom),
               ["--comp", "/shoot/Anna.comp", "--neutral-gray"] + tail, "自选: modules by name")
        var dr5OnProject = RetouchSettings(preset: .dr5)
        dr5OnProject.set("sculpt", false)
        expect(Arguments.problem(with: dr5OnProject, for: existing), nil, "DR5 can go on a project")
        expect(Arguments.command(for: existing, input: project, project: project, settings: dr5OnProject),
               ["--comp", "/shoot/Anna.comp", "--delicious-skin", "--freq-sep", "--dual-curves", "--inspection", "--eyes",
                "--grain", "--render", "--progress"], "on a project, DR5's modules go one by one")
        expect(Arguments.command(for: existing, input: project, project: project, settings: RetouchSettings(preset: .studio), inCompositor: true),
               ["--comp", "/shoot/Anna.comp", "--freq-sep", "--neutral-gray", "--dual-curves", "--inspection", "--progress"],
               "open in Compositor: no preview render; 影棚 on a project, module by module")

        let heic = URL(fileURLWithPath: "/tmp/X.tif")
        expect(Array(Arguments.command(for: RetouchJob(source: URL(fileURLWithPath: "/shoot/B.heic")), input: heic,
                                       project: URL(fileURLWithPath: "/shoot/B.comp"), settings: RetouchSettings()).prefix(2)),
               ["--init", "/tmp/X.tif"], "HEIC starts from its converted copy")

        print("== glow")
        settings = RetouchSettings()
        expect(Arguments.glowSpec(settings), nil, "no glow")
        settings.glow = .mist
        expect(Arguments.glowSpec(settings), "mist", "mist, natural color")
        settings.glow = .halation
        expect(Arguments.glowSpec(settings), "halation:natural", "halation, natural color")
        settings.glowColorMode = .single
        settings.glowColor = RGB(red: 1, green: 0.5, blue: 0)
        expect(Arguments.glowSpec(settings), "halation:#FF8000", "one color as #RRGGBB")
        settings.glow = .mist
        settings.glowColorMode = .twoTone
        settings.glowOuterColor = RGB.named[4].1
        expect(Arguments.glowSpec(settings), "mist:#FF8000,#9CCBFF", "two colors, inner then outer")
        settings.glow = .dream
        expect(Arguments.glowSpec(settings), "dream", "dream takes no color")
        expect(RGB(red: 1.2, green: -0.1, blue: 0.5).hex, "#FF0080", "colors clamp")
        expect(RGB.named.map(\.1.hex), ["#FFD08A", "#FF994E", "#FF8FB8", "#B8A0FF", "#9CCBFF", "#6FE3D6"],
               "the named swatches are the skill's GLOW_COLORS (gold to teal)")

        print("== what can run")
        expect(Arguments.problem(with: RetouchSettings(preset: .none), for: job), "还没有选择任何效果", "nothing picked")
        expect(Arguments.problem(with: RetouchSettings(preset: .none, look: .hk), for: job), nil, "a look alone is enough")
        expect(Arguments.problem(with: RetouchSettings(preset: .none, glow: .mist), for: job), nil, "a glow alone is enough")
        expect(Arguments.problem(with: RetouchSettings(preset: .studio), for: existing), nil, "影棚 adds to a project")
        var refreshing = existing
        refreshing.refresh = true
        expect(Arguments.problem(with: RetouchSettings(preset: .none), for: refreshing), nil, "重算 needs no settings")
        var photoRefresh = job
        photoRefresh.refresh = true
        expect(Arguments.problem(with: RetouchSettings(), for: photoRefresh), "只有工程能重算", "a photo can't be recomputed")
        let manifest = { (layers: String) in Data("{\"layers\": [\(layers)]}".utf8) }
        let smoothing = #"{"name": "🔥 [DR5] 保边磨皮 · 保形 · 祛痘 6 处 · 保留毛孔 (黑蒙版 · 画笔涂白处磨皮)", "imageFile": "A.png", "blendMode": "Normal"}"#
        expect(Arguments.refreshable(manifest: manifest(smoothing)), true, "a smoothing layer can be recomputed")
        expect(Arguments.refreshable(manifest: manifest(#"{"name": "👁️ [DR5] 眼神光 · 虹膜锐化 (已按五官预涂 · 可擦改)", "imageFile": "B.png"}"#)),
               true, "the eye layer, in Normal")
        expect(Arguments.refreshable(manifest: manifest(#"{"name": "👁️ [DR5] 眼神光 · 虹膜锐化", "imageFile": "B.png", "blendMode": "Screen"}"#)),
               false, "...not one the user changed to another mode")
        expect(Arguments.refreshable(manifest: manifest(#"{"name": "🩹 自动去瑕疵 · 6 处 (想保留的痣, 在蒙版上擦掉)", "imageFile": "A.png", "blendMode": "Normal"}"#)),
               false, "an earlier version's heal layer is left alone now")
        expect(Arguments.refreshable(manifest: manifest(#"{"name": "🔥 磨皮", "imageFile": "A.png"}"#)), true,
               "a smoothing layer the user renamed, emoji kept, is still one")
        expect(Arguments.smoothingKinds(manifest: manifest(#"{"name": "🔥 磨皮", "imageFile": "A.png"}"#)), [.auto], "...on the skin mask")
        expect(Arguments.smoothingKinds(manifest: manifest(#"{"name": "🔥✋ 磨皮", "imageFile": "A.png"}"#)), [.manual], "...brushed on, by the ✋")
        expect(Arguments.smoothingKinds(manifest: manifest(smoothing)), [.manual], "...or by the words earlier versions used")
        expect(Arguments.smoothingKinds(manifest: manifest(smoothing + "," + #"{"name": "🔥 [DR5] 保边磨皮 (面部锁定)", "imageFile": "C.png"}"#)),
               [.auto, .manual], "both kinds in one project")
        expect(Arguments.smoothingKinds(manifest: manifest(#"{"name": "🩶 中性灰", "imageFile": "D.png", "blendMode": "Soft Light"}"#)), [],
               "no smoothing layer")
        expect(Arguments.failure("project changed on disk while this ran (saved by Compositor?): nothing was changed", status: 1)
               .contains("又保存了"), true, "a save that landed mid-run, in words")
        expect(Arguments.refreshable(manifest: manifest(#"{"name": "🩶 中性灰 D&B (50% 灰 · 柔光)", "imageFile": "C.png", "blendMode": "Soft Light"}"#
                                                        + "," + #"{"name": "🔥 我的文件夹", "isGroup": true}"#)),
               false, "gray layers the user paints on, and folders, are not")
        expect(Arguments.refreshable(manifest: Data("not json".utf8)), false, "an unreadable manifest is not")

        print("== naming")
        var taken: Set<String> = []
        let name = { Arguments.newProject(for: photo, in: nil) { taken.contains($0.path) }.lastPathComponent }
        expect(name(), "Anna.comp", "free name")
        taken = ["/shoot/Anna.comp"]
        expect(name(), "Anna 2.comp", "taken project")
        taken = ["/shoot/Anna.comp", "/shoot/Anna 2.render.jpg"]
        expect(name(), "Anna 3.comp", "a leftover render counts as taken")
        taken = ["/shoot/Anna.checks"]
        expect(name(), "Anna 2.comp", "a leftover checks folder counts as taken")
        expect(Arguments.newProject(for: photo, in: URL(fileURLWithPath: "/out")) { _ in false }.path, "/out/Anna.comp",
               "a chosen folder")
        expect(Arguments.renderImage(of: project).path, "/shoot/Anna.render.jpg", "render beside the project")
        expect(Arguments.checksFolder(of: URL(fileURLWithPath: "/shoot/Anna 2.comp")).path, "/shoot/Anna 2.checks",
               "checks beside the project")

        print("== the script's output")
        expect(Arguments.parse("[STEP] 2/7 delicious_skin").kind, .step, "step line")
        expect(Arguments.parse("[WARN] no face found").text, "no face found", "warning text")
        expect(Arguments.parse("[Error] cannot read nope.jpg").kind, .error, "error line")
        expect(Arguments.parse("  Stack, top to bottom:").kind, .other, "other line")
        expect(Arguments.step("1/4 freq_sep")?.title, "高低频", "module title from its key")
        expect(Arguments.step("1/4 freq_sep")?.fraction, 0, "first step starts at 0")
        expect(Arguments.step("3/4 delicious_skin")?.title, "DR5 磨皮", "underscore keys map to flags")
        expect(Arguments.step("2/4 neutral_gray")?.title, "中性灰", "neutral gray")
        expect(Arguments.step("4/4 look")?.title, "调色", "look")
        expect(Arguments.step("1/1 refresh")?.title, "重算自动层", "refresh")
        expect(Arguments.step("render")?.title, "渲染预览", "render")
        expect(Arguments.step("checks")?.title, "生成检查图", "checks")
        expect(Arguments.step("garbage") == nil, true, "anything else is no step")
        let order = (1...5).compactMap { Arguments.step("\($0)/5 freq_sep")?.fraction } + ["render", "checks"].compactMap { Arguments.step($0)?.fraction }
        expect(order == order.sorted() && order.last! < 1, true, "steps only move forward, short of done")
        expect(Arguments.notice("Downloading the hair and skin segmentation model (16 MB, first run only)"),
               "首次下载人像分割模型（16 MB）", "first-run download shown")
        expect(Arguments.notice("+ Curves (Brighten)"), nil, "layer lines stay in the log")

        expect(Arguments.failure("cannot read /a/b.jpg (cannot identify image file)", status: 1).hasPrefix("读不了这个文件"), true,
               "an unreadable photo, in words")
        expect(Arguments.failure("cannot read /a/b.comp/manifest.json: Expecting value", status: 1), "工程文件损坏，读不了它的 manifest.json",
               "a broken project is not called a photo")
        expect(Arguments.failure("something new", status: 1), "something new", "anything else as the script said it")
        expect(Arguments.failure(nil, status: -9).contains("-9"), true, "a crash shows its code")

        print("== the queue")
        expect(RetouchJob.accepts(URL(fileURLWithPath: "/a/B.HEIC")), true, "HEIC, any case")
        expect(RetouchJob.accepts(URL(fileURLWithPath: "/a/B.comp")), true, "projects")
        expect(RetouchJob.accepts(URL(fileURLWithPath: "/a/B.psd")), false, "not PSD")
        expect(Preset.daily.includes, ["delicious-skin", "neutral-gray", "inspection"], "常用's modules: the everyday flow")
        expect(Preset.dr5.includes, ["freq-sep", "delicious-skin", "sculpt", "dual-curves", "eyes", "grain", "inspection"],
               "DR5's modules (no teeth whitening any more)")
        expect(Preset.studio.includes, ["freq-sep", "neutral-gray", "dual-curves", "inspection"], "影棚's modules")
        expect(Preset.allCases.map(\.rawValue), ["none", "daily", "dr5", "studio"], "the presets")
        expect(Preset.daily.needsPhoto, false, "常用 goes on a project as a preset")

        print("== remembered settings")
        let keptFolder = FileManager.default.temporaryDirectory.appendingPathComponent("remember-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: keptFolder, withIntermediateDirectories: true)
        var kept = RetouchSettings(preset: .dr5, modules: ["neutral-gray"], removed: ["grain"], smoothingMask: .manual,
                                   skinStrength: .light, strength: 0.7, texture: .soft, look: .film, glow: .mist,
                                   glowColorMode: .twoTone, glowColor: RGB.named[1].1,  // #RRGGBB keeps n/255 exactly
                                   glowOuterColor: RGB.named[4].1, render: false, checks: false, output: .folder(keptFolder))
        expect(RetouchSettings(dictionary: kept.asDictionary), kept, "everything survives the round trip")
        expect(RetouchSettings(dictionary: [:]), RetouchSettings(), "nothing kept: the defaults")
        expect(RetouchSettings(dictionary: kept.asDictionary, folderExists: { _ in false }).output, .besidePhoto,
               "an output folder that is gone falls back to beside the photo")
        let stale: [String: Any] = ["preset": "auto", "modules": ["heal", "eyes", "folds"], "removed": ["sculpt"],
                                    "strength": 9.0, "glowColor": "purple", "look": "vintage", "render": false]
        let read = RetouchSettings(dictionary: stale)
        expect(read.preset, .daily, "a preset of another version falls back to 常用")
        expect(read.modules, ["eyes"], "modules that no longer exist are dropped")
        expect(read.removed, [], "a removed module the preset doesn't have is dropped")
        expect(read.strength == 1 && read.glowColor == RGB.named[0].1 && read.look == .none && read.render == false, true,
               "out-of-range and unknown values stay at their defaults, the rest is read")
        let suite = "logic-tests-\(UUID().uuidString)"
        if let defaults = UserDefaults(suiteName: suite) {
            kept.output = .besidePhoto
            kept.save(to: defaults)
            expect(RetouchSettings.load(from: defaults), kept, "saved to and loaded from UserDefaults")
            defaults.removePersistentDomain(forName: suite)
            expect(RetouchSettings.load(from: defaults), RetouchSettings(), "nothing saved: the defaults")
        }
        try? FileManager.default.removeItem(at: keptFolder)
        expect(RGB(hex: "#FF8000"), RGB(red: 1, green: 0.5019607843137255, blue: 0), "a color from its hex")
        expect(RGB(hex: "FF80") == nil && RGB(hex: "#GGGGGG") == nil, true, "...and nil for anything else")
        let flags = Set(ModuleGroup.allCases.flatMap(\.modules).map(\.flag))
        expect(flags, ["delicious-skin", "freq-sep", "neutral-gray", "dual-curves", "inspection", "sculpt", "eyes"],
               "the module switches left")
        expect(Preset.allCases.allSatisfy { $0.includes.subtracting(["grain"]).isSubset(of: flags) }, true,
               "every preset module has a switch")

        print("== Compositor")
        let comp = URL(fileURLWithPath: "/shoot/Anna.comp")
        let front = { (windows: [CompositorLink.Window]?) in
            CompositorLink.front(installed: true, running: true, trusted: true, windows: windows)
        }
        let titled = { (title: String?, open: [URL], recent: [URL]) in  // a document window telling no file, only its title
            CompositorLink.front(installed: true, running: true, trusted: true, windows: [(true, nil, title)], open: open, recent: recent)
        }
        MainActor.assumeIsolated {
            let plain = Studio(environment: PythonEnvironment(support: FileManager.default.temporaryDirectory))
            expect(plain.remembers, false, "a studio made for tests forgets its settings")
            expect(UserDefaults.standard.object(forKey: "autoSave") == nil ? plain.autoSave : true, true,
                   "saving in Compositor first is on until switched off")
        }
        expect(CompositorLink.front(installed: false, running: false, trusted: true, windows: nil), .notInstalled, "not installed")
        expect(CompositorLink.front(installed: true, running: false, trusted: false, windows: nil), .notRunning, "not running")
        expect(CompositorLink.front(installed: true, running: true, trusted: false, windows: nil), .noAccess, "running, no permission")
        expect(front([]), .noWindow, "no window")
        expect(front([(true, comp, "Anna"), (true, URL(fileURLWithPath: "/b.comp"), "b")]), .project(comp), "the front window's project")
        expect(front([(false, nil, nil), (true, comp, "Anna")]), .project(comp), "a panel in front is passed over")
        expect(front([(true, nil, "未命名"), (true, comp, "Anna")]), .untitled, "an unsaved document in front is untitled, not the one behind")
        expect(front([(true, URL(fileURLWithPath: "/a/photo.jpg"), "photo.jpg")]), .untitled, "a document that isn't a project")
        expect(front([(false, comp, nil)]), .project(comp), "no ordinary window: any window with a project")
        expect(front([(true, nil, nil)]), .untitled, "no file, no title, nothing held open: untitled")

        // Compositor's windows tell no file (AXDocument comes back empty), only a title: the project's name
        let other = URL(fileURLWithPath: "/other/Anna.comp")
        expect(titled("Anna", [], [comp]), .project(comp), "titled like a recent project")
        expect(titled("Anna", [comp], []), .project(comp), "titled like a project held open")
        expect(titled("Anna", [other], [comp]), .project(other), "...the one held open before a recent one of the same name")
        expect(titled("Anna — 已编辑", [], [comp]), .project(comp), "an edited mark after the name")
        expect(titled("Anna.comp", [], [comp]), .project(comp), "the name with its extension")
        expect(titled("anna", [], [comp]), .project(comp), "the case aside")
        let accented = URL(fileURLWithPath: "/shoot/Anne\u{301}.comp")  // decomposed, as a file name may be spelled
        expect(titled("Ann\u{E9}", [], [accented]), .project(accented), "a composed title, a decomposed file name")
        expect(titled("未命名 2", [comp], []), .untitled, "an untitled document in front, a project held open behind")
        expect(titled("Untitled", [comp], []), .untitled, "...in English")
        expect(titled("IMG_0001.jpg", [comp], [comp]), .untitled, "a photo dropped in, not saved as a project yet")
        expect(titled("Beth", [comp], []), .project(comp), "an unmatched title with one project held open: that one")
        expect(titled("Beth", [comp, other], []), .unlocated("Beth"), "...with several held open: unlocated")
        expect(titled("Beth", [], [comp]), .unlocated("Beth"), "...with none held open: unlocated")
        expect(titled(nil, [comp], []), .project(comp), "no title readable, one project held open")
        expect(CompositorLink.front(installed: true, running: true, trusted: false, windows: nil, open: [comp]), .project(comp),
               "without permission, the one project held open")
        expect(CompositorLink.front(installed: true, running: true, trusted: false, windows: nil, open: [comp, other]), .noAccess,
               "...several held open: no access, as before")
        expect(CompositorLink.titleCandidates("A - B — C"), ["A - B — C", "A - B", "A"], "title candidates: whole, then cut at each separator")
        expect(CompositorLink.looksUntitled("未命名文稿 3"), true, "untitled names with a number")
        expect(CompositorLink.looksUntitled("Anna"), false, "a name")
        expect(CompositorLink.documentURL("file:///shoot/Anna.comp")?.path, comp.path, "AXDocument as a file URL")
        expect(CompositorLink.documentURL("/shoot/Anna.comp")?.path, comp.path, "...as a plain path")
        expect(CompositorLink.documentURL("file:///shoot/My%20Anna.comp")?.path, "/shoot/My Anna.comp", "...percent-encoded")
        expect(CompositorLink.documentURL("file:///shoot/My Anna.comp")?.path, "/shoot/My Anna.comp", "...with the space unescaped")
        expect(CompositorLink.documentURL("file://localhost/shoot/Anna.comp")?.path, comp.path, "...with a host")
        expect(CompositorLink.documentURL("https://example.com/x.comp"), nil, "not a file")
        expect(CompositorLink.documentURL(""), nil, "empty")
        expect(CompositorLink.isSaveItem(char: "S", modifiers: 0), true, "File › Save: ⌘S")
        expect(CompositorLink.isSaveItem(char: "s", modifiers: nil), true, "...whatever the case, no modifiers reported")
        expect(CompositorLink.isSaveItem(char: "S", modifiers: 1), false, "⇧⌘S is Save As")
        expect(CompositorLink.isSaveItem(char: "S", modifiers: 2), false, "⌥⌘S is Save a Copy")
        expect(CompositorLink.isSaveItem(char: "O", modifiers: 0), false, "⌘O is Open")
        expect(CompositorLink.isSaveItem(char: nil, modifiers: 0), false, "no shortcut")
        expect(Studio.saveNote(.saved).contains("已替你"), true, "the log says it saved")
        expect(Studio.saveNote(.noAccess).contains("⌘S") && Studio.saveNote(.unreadable).contains("⌘S"), true,
               "...or asks for ⌘S when it couldn't")

        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("recents-\(UUID().uuidString)")
        let make = { (name: String) -> URL in
            let url = folder.appendingPathComponent(name)
            if name.hasSuffix(".comp") { try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true) }
            else { try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true); try? Data().write(to: url) }
            return url
        }
        let newest = make("B.comp"), picture = make("C.jpg"), gone = make("D.comp"), older = make("A.comp")
        let items: [[String: Any]] = [newest, picture, gone, older, newest].map { url in
            ["Bookmark": (try? url.bookmarkData()) ?? Data(), "uuid": UUID().uuidString, "visibility": 0]
        }
        try? FileManager.default.removeItem(at: gone)
        let list = folder.appendingPathComponent("recent.sfl4")
        try? NSKeyedArchiver.archivedData(withRootObject: ["items": items, "properties": [String: Any]()] as NSDictionary,
                                          requiringSecureCoding: true).write(to: list)
        expect(CompositorLink.recentProjects(in: list).map(\.lastPathComponent), ["B.comp", "A.comp"],
               "recent projects: newest first, photos, missing and repeated ones left out")
        expect(CompositorLink.recentProjects(in: folder.appendingPathComponent("none.sfl4")), [], "no list, no projects")

        // the projects a process holds files of, from the kernel: this process's own here
        let held = folder.appendingPathComponent("照片 nian1.comp", isDirectory: true)
        try? FileManager.default.createDirectory(at: held.appendingPathComponent("images"), withIntermediateDirectories: true)
        try? Data("{}".utf8).write(to: held.appendingPathComponent("manifest.json"))
        try? Data().write(to: held.appendingPathComponent("images/a.png"))
        let hollow = folder.appendingPathComponent("hollow.comp", isDirectory: true)  // no manifest: not a project
        try? FileManager.default.createDirectory(at: hollow, withIntermediateDirectories: true)
        try? Data().write(to: hollow.appendingPathComponent("x.txt"))
        let fds = [held.appendingPathComponent("images/a.png"), held.appendingPathComponent("manifest.json"), hollow.appendingPathComponent("x.txt")]
            .map { open($0.path, O_RDONLY) }
        expect(fds.allSatisfy { $0 >= 0 }, true, "fixture files held open")
        let mine = CompositorLink.openProjects(of: getpid())
        expect(mine.map(CompositorLink.key), [CompositorLink.key(held)],
               "the package held open, once, by any file inside it; a .comp folder without a manifest isn't one")
        expect(CompositorLink.front(installed: true, running: true, trusted: true, windows: [(true, nil, "照片 nian1")], open: mine),
               mine.first.map(CompositorLink.Front.project) ?? .noWindow, "a Chinese title meets the package held open")
        expect(CompositorLink.front(installed: true, running: true, trusted: false, windows: nil, open: mine),
               mine.first.map(CompositorLink.Front.project) ?? .noWindow, "...and without permission to read the windows")
        fds.forEach { close($0) }
        expect(CompositorLink.openProjects(of: getpid()), [], "closed: no longer held open")
        expect(CompositorLink.openProjects(of: 1), [], "another user's process: nothing, quietly")
        try? FileManager.default.removeItem(at: folder)

        MainActor.assumeIsolated {
            let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("same-\(UUID().uuidString).comp")
            try? FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
            let other = URL(fileURLWithPath: "/elsewhere/B.comp")
            let viaPrivate = URL(fileURLWithPath: tmp.resolvingSymlinksInPath().path)  // /private/var/... spelling
            let link = CompositorLink(front: .project(viaPrivate), recent: [tmp, other])
            expect(link.otherRecent, [other], "the project being edited isn't listed again as recent, however its path is spelled")
            let shared = Studio(environment: PythonEnvironment(support: folder), compositor: link)
            shared.retouchNow(viaPrivate)
            shared.retouchNow(tmp)
            expect(shared.jobs.count, 1, "the card and the recent list queue the same project once")
            try? FileManager.default.removeItem(at: tmp)

            let queueing = Studio(environment: PythonEnvironment(support: folder))
            queueing.environment.state = .ready  // ready, but no script here: start() returns, jobs stay queued
            let a = URL(fileURLWithPath: "/shoot/A.comp"), b = URL(fileURLWithPath: "/shoot/B.comp")
            let wasAuto = queueing.autoQueue
            queueing.autoQueue = false
            queueing.compositorChanged(.project(a))
            expect(queueing.jobs.count, 0, "auto-queue off: switching photos queues nothing")
            queueing.autoQueue = true
            queueing.compositorChanged(.project(a))
            queueing.compositorChanged(.untitled)
            queueing.compositorChanged(.project(b))
            expect(queueing.jobs.map(\.source), [a, b], "auto-queue on: each photo brought to the front joins the queue")
            expect(queueing.place(of: queueing.jobs[1].id), 2, "and knows its place in line")
            expect(queueing.pendingCount, 2, "two to do")
            queueing.jobs[0].status = .done
            queueing.compositorChanged(.project(a))
            expect(queueing.jobs.count == 2 && queueing.jobs[0].status == .done, true,
                   "a photo already done isn't queued again by switching back (that would stack a second set of layers)")
            expect(queueing.place(of: queueing.jobs[1].id), 1, "B moves up")
            queueing.autoQueue = wasAuto

            let following = Studio(environment: PythonEnvironment(support: folder))
            let manualProject = folder.appendingPathComponent("Manual.comp")
            try? FileManager.default.createDirectory(at: manualProject, withIntermediateDirectories: true)
            try? Data(#"{"layers": [{"name": "🔥✋ 磨皮", "imageFile": "A.png"}]}"#.utf8).write(to: manualProject.appendingPathComponent("manifest.json"))
            following.settings.smoothingMask = .auto
            following.compositorChanged(.project(manualProject))
            expect(following.settings.smoothingMask, .manual, "磨皮方式 follows the project brought to the front")
            expect(following.smoothingNote(for: manualProject)?.contains("已按") == true, true, "...and says so under the button")
            following.settings.smoothingMask = .auto
            expect(following.smoothingNote(for: manualProject)?.contains("多加一层") == true, true,
                   "...and warns when the choice differs from the project's")
            following.compositorChanged(.untitled)
            expect(following.smoothingNote(for: manualProject)?.contains("多加一层") == true, true, "the warning stays while they differ")
            try? FileManager.default.removeItem(at: manualProject)

            let studio = Studio(environment: PythonEnvironment(support: folder))  // not ready: nothing starts
            studio.retouchNow(comp)
            expect(studio.jobs.map(\.source), [comp], "精修 queues the project")
            expect(studio.selection == studio.jobs.first?.id, true, "and selects it")
            studio.jobs[0].status = .done
            studio.retouchNow(comp)
            expect(studio.jobs.count == 1 && studio.jobs[0].status == .waiting, true, "again: the same entry goes back in line")
            studio.jobs[0].status = .done
            studio.refreshNow(comp)
            expect(studio.jobs.count == 1 && studio.jobs[0].status == .waiting && studio.jobs[0].refresh, true,
                   "重算 puts the same entry back in line, as a recompute")
            studio.jobs[0].status = .done
            studio.retouchNow(comp)
            expect(studio.jobs[0].refresh, false, "精修 after it adds layers again")
        }

        print(failures == 0 ? "PASS logic" : "FAIL logic: \(failures)")
        exit(failures == 0 ? 0 : 1)
    }
}
