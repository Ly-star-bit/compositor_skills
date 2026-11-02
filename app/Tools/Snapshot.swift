// Pictures of the app's screens and its icon, made without showing anything: each view is laid out in a window that
// is never put on screen (the process can't even become active), drawn into a bitmap and saved as PNG. Liquid Glass
// is composited by the window server, so it can't appear here; the views draw a material in its place (snapshotMode).
// Built and run by Tools/snapshot.sh with the app's sources (all but App.swift).
//
//   Snapshot screens <out folder> <photo folder> [<finished e2e folder>]
//   Snapshot icon <out.png>
//   Snapshot dmg <out.png>                       the disk image window's background (and <out>@2x.png)
//   Snapshot dmg-preview <background> <out.png>  that window as Finder should show it, light and dark
import AppKit
import SwiftUI

@main
enum Snapshot {
    @MainActor static func main() {
        NSApplication.shared.setActivationPolicy(.prohibited)  // no Dock icon, no menu bar, never frontmost
        let args = CommandLine.arguments
        switch args.count > 2 ? args[1] : "" {
        case "icon": icon(to: URL(fileURLWithPath: args[2]))
        case "dmg": dmgBackground(to: URL(fileURLWithPath: args[2]))
        case "dmg-preview" where args.count > 3: dmgPreview(background: URL(fileURLWithPath: args[2]), to: URL(fileURLWithPath: args[3]))
        case "screens" where args.count > 3:
            screens(to: URL(fileURLWithPath: args[2]), photos: URL(fileURLWithPath: args[3]),
                    finished: args.count > 4 ? URL(fileURLWithPath: args[4]) : nil)
        default:
            print("usage: Snapshot screens <out> <photos> [<e2e folder>] | Snapshot icon <out.png>")
            exit(2)
        }
    }

    // MARK: drawing

    @MainActor static func shoot<V: View>(_ view: V, size: NSSize, dark: Bool = false, to url: URL) {
        let controller = NSHostingController(rootView: view.environment(\.snapshotMode, true))
        controller.sceneBridgingOptions = [.toolbars, .title]
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.toolbarStyle = .unified
        window.contentViewController = controller
        window.setContentSize(size)  // never ordered front: it stays off screen
        for _ in 0..<8 {
            RunLoop.current.run(until: Date().addingTimeInterval(0.12))
            window.contentView?.layoutSubtreeIfNeeded()
        }
        let frame = window.contentView?.superview ?? window.contentView!
        guard let rep = frame.bitmapImageRepForCachingDisplay(in: frame.bounds) else { print("nothing drawn: \(url.lastPathComponent)"); return }
        frame.cacheDisplay(in: frame.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
        print("\(url.lastPathComponent)  \(rep.pixelsWide)x\(rep.pixelsHigh)  on screen: \(window.isVisible)")
        window.close()
    }

    // MARK: screens

    @MainActor static func screens(to out: URL, photos: URL, finished: URL?) {
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let fm = FileManager.default
        let photoFiles = ((try? fm.contentsOfDirectory(at: photos, includingPropertiesForKeys: nil)) ?? [])
            .filter { RetouchJob.accepts($0) }.sorted { $0.path < $1.path }
        let projects = (finished.flatMap { try? fm.contentsOfDirectory(at: $0, includingPropertiesForKeys: nil) } ?? [])
            .filter { $0.pathExtension == "comp" }.sorted { $0.path < $1.path }
        let size = NSSize(width: 1360, height: 860)

        func environment(_ state: PythonEnvironment.State) -> PythonEnvironment {
            let environment = PythonEnvironment(support: out.appendingPathComponent("support"))
            environment.state = state
            environment.interpreterDescription = "Python 3.14.6"
            environment.hasSegmentationModel = true
            return environment
        }

        // first launch
        shoot(RootView(studio: Studio(environment: environment(.missing))), size: size, to: out.appendingPathComponent("1-setup.png"))
        shoot(RootView(studio: Studio(environment: environment(.installing("下载 NumPy、OpenCV、Pillow", 0.45)))), size: size,
              to: out.appendingPathComponent("2-installing.png"))
        let failedSetup = environment(.failed("「下载 NumPy、OpenCV、Pillow」没有完成。请检查网络后重试，详细记录在安装日志里。"))
        failedSetup.log = "== 创建独立的 Python 环境\n== 更新安装工具\n== 下载 NumPy、OpenCV、Pillow\nERROR: Could not find a version that satisfies the requirement numpy\n(exit 1)\n"
        shoot(RootView(studio: Studio(environment: failedSetup)), size: size, to: out.appendingPathComponent("2b-setup-failed.png"))

        // ready, nothing queued: Compositor closed with recent projects, then each state of the Compositor card
        func link(_ front: CompositorLink.Front) -> Studio {
            Studio(environment: environment(.ready), compositor: CompositorLink(front: front, recent: projects))
        }
        shoot(Columns(studio: link(.notRunning)), size: size, to: out.appendingPathComponent("3-empty.png"))
        if let project = projects.first {
            shoot(Columns(studio: link(.project(project))), size: size, to: out.appendingPathComponent("3b-compositor-project.png"))
            shoot(Columns(studio: link(.project(project))), size: size, dark: true, to: out.appendingPathComponent("3c-compositor-dark.png"))
            shoot(RootView(studio: link(.project(project))), size: size, to: out.appendingPathComponent("3d-compositor-window.png"))
            let unread = Studio(environment: environment(.ready), compositor: CompositorLink(front: .project(project), recent: projects, trusted: false))
            shoot(Columns(studio: unread), size: size, to: out.appendingPathComponent("3h-compositor-project-no-access.png"))
        }
        if let project = projects.first {
            // the floating ball: nothing found, a project found, retouching it, just done; then its panel
            let found = link(.project(project))
            let running = link(.project(project))
            var job = RetouchJob(source: project)
            job.status = .running; job.step = "DR5 磨皮"; job.fraction = 0.45
            running.jobs = [job]; running.current = job.id
            let done = link(.project(project))
            job.status = .done; job.finished = Date(); job.seconds = 8.3; job.fraction = nil
            done.jobs = [job]
            let balls = HStack(spacing: 18) {
                BallView(studio: link(.notRunning)); BallView(studio: found); BallView(studio: running); BallView(studio: done)
            }
            .padding(20)
            shoot(balls.background(Color(white: 0.16)), size: NSSize(width: 460, height: 130), dark: true,
                  to: out.appendingPathComponent("9-balls-on-dark.png"))
            shoot(balls.background(Color(white: 0.92)), size: NSSize(width: 460, height: 130),
                  to: out.appendingPathComponent("9a-balls-on-light.png"))
            let picker = FloatingMode.pickerSize
            found.settings.set("neutral-gray", true)
            found.settings.smoothingMask = .manual
            found.settings.look = .film
            shoot(FloatingPicker(studio: found).background(Color(white: 0.85)), size: picker, to: out.appendingPathComponent("9b-picker.png"))
            shoot(FloatingPicker(studio: running).background(Color(white: 0.15)), size: picker, dark: true,
                  to: out.appendingPathComponent("9c-picker-running-dark.png"))
            shoot(FloatingPicker(studio: done).background(Color(white: 0.85)), size: picker, to: out.appendingPathComponent("9d-picker-done.png"))
            shoot(FloatingPicker(studio: link(.noAccess)).background(Color(white: 0.85)), size: picker,
                  to: out.appendingPathComponent("9e-picker-no-access.png"))
        }
        shoot(Columns(studio: link(.noAccess)), size: size, to: out.appendingPathComponent("3e-compositor-no-access.png"))
        shoot(Columns(studio: link(.untitled)), size: size, to: out.appendingPathComponent("3f-compositor-untitled.png"))
        shoot(Columns(studio: link(.unlocated("nian1"))), size: size, to: out.appendingPathComponent("3g-compositor-unlocated.png"))

        // a batch under way
        let studio = Studio(environment: environment(.ready),
                            compositor: CompositorLink(front: projects.first.map { .project($0) } ?? .notRunning, recent: projects))
        var jobs: [RetouchJob] = []
        for (index, project) in projects.prefix(2).enumerated() {
            let photo = photoFiles.first { $0.deletingPathExtension().lastPathComponent == project.deletingPathExtension().lastPathComponent }
            var job = RetouchJob(source: photo ?? project)
            job.status = .done
            job.project = project
            job.render = Arguments.renderImage(of: project)
            job.checks = Studio.checkImages(in: Arguments.checksFolder(of: project))
            job.seconds = [41.6, 38.2][index]
            job.step = "完成"
            job.warnings = index == 1 ? ["Apple Vision's person segmentation leaves out the face it found, so it isn't used: the skin mask isn't bounded by it"] : []
            job.log = "[STEP] 1/7 freq_sep\n[OK] Faces found: 1 (Apple Vision)\n[STEP] 2/7 delicious_skin\n…\n[SUCCESS] \(project.path): 17 layers, 41.6s\n"
            jobs.append(job)
        }
        let names = ["婚纱_0412.jpg", "证件照_女_03.heic", "外景_0088.jpg", "棚拍_0102.jpg"]
        for (index, photo) in photoFiles.enumerated() where jobs.count < 6 {
            var job = RetouchJob(source: photo)
            if index == 0 { job.status = .running; job.step = "DR5 磨皮"; job.fraction = 0.34 }
            if index == 1 { job.status = .failed("cannot read \(names[1]): not a photo") ; job.step = "失败"; job.seconds = 0.4 }
            jobs.append(job)
        }
        _ = names
        studio.jobs = jobs
        studio.selection = jobs.first?.id
        studio.isRunning = true
        studio.batch = jobs.map(\.id)
        studio.current = jobs.first { $0.status == .running }?.id
        studio.settings.look = .film
        shoot(RootView(studio: studio), size: size, to: out.appendingPathComponent("4-batch.png"))
        shoot(RootView(studio: studio), size: size, dark: true, to: out.appendingPathComponent("4b-batch-dark.png"))
        shoot(Columns(studio: studio), size: size, to: out.appendingPathComponent("4c-columns.png"))
        shoot(Columns(studio: studio), size: size, dark: true, to: out.appendingPathComponent("4d-columns-dark.png"))
        // the whole inspector, every card, which the window shows only by scrolling
        studio.settings.texture = .soft
        shoot(InspectorView(studio: studio), size: NSSize(width: 340, height: 2300),
              to: out.appendingPathComponent("4e-inspector-full.png"))
        studio.settings.texture = .natural

        studio.selection = studio.current
        shoot(RootView(studio: studio), size: size, to: out.appendingPathComponent("5-running.png"))
        shoot(Columns(studio: studio), size: size, to: out.appendingPathComponent("5c-columns-running.png"))
        studio.selection = jobs.first { if case .failed = $0.status { true } else { false } }?.id
        studio.isRunning = false
        studio.current = nil
        shoot(RootView(studio: studio), size: size, to: out.appendingPathComponent("6-failed.png"))
        if let checked = jobs.first?.id {
            studio.selection = checked
            shoot(RootView(studio: studio), size: size, dark: true, to: out.appendingPathComponent("6b-done-dark.png"))
        }

        // the whole inspector, every card, with the glow's color options open
        let tall = Studio(environment: environment(.ready))
        tall.jobs = jobs.filter { $0.status == .done }
        tall.settings.preset = .dr5
        tall.settings.modules = ["neutral-gray"]
        tall.settings.set("grain", false)
        tall.settings.smoothingMask = .manual
        tall.settings.look = .coolFair
        tall.settings.glow = .mist
        tall.settings.glowColorMode = .twoTone
        tall.settings.glowOuterColor = RGB.named[4].1
        tall.settings.strength = 0.8
        shoot(InspectorView(studio: tall).frame(width: 340, height: 2000), size: NSSize(width: 340, height: 2000),
              to: out.appendingPathComponent("7-inspector.png"))
        shoot(InspectorView(studio: tall).frame(width: 340, height: 2000), size: NSSize(width: 340, height: 2000), dark: true,
              to: out.appendingPathComponent("7b-inspector-dark.png"))

        shoot(SettingsView(environment: environment(.ready)), size: NSSize(width: 520, height: 400),
              to: out.appendingPathComponent("8-settings.png"))

        // the PhotoCraft tab: nothing queued, then a document made and one added to (from the finished folder's .pcraft)
        let craft = Studio(environment: environment(.ready))
        craft.target = .photocraft
        shoot(TabbedColumns(studio: craft), size: NSSize(width: 1360, height: 900), to: out.appendingPathComponent("10-photocraft-empty.png"))
        let documents = (finished.flatMap { try? fm.contentsOfDirectory(at: $0, includingPropertiesForKeys: nil) } ?? [])
            .filter { $0.pathExtension == "pcraft" }.sorted { $0.path < $1.path }
        if let document = documents.first {
            var made = RetouchJob(source: photoFiles.first ?? document, target: .photocraft)
            made.status = .done; made.project = document; made.seconds = 5.0; made.finished = Date(); made.step = "完成"
            let render = Arguments.renderImage(of: document)
            made.render = fm.fileExists(atPath: render.path) ? render : nil
            made.checks = Studio.checkImages(in: Arguments.checksFolder(of: document))
            made.log = "[STEP] 1/2 builder\n[STEP] 2/2 photocraft\n[SUCCESS] \(document.path): 19 layers, 5.0s\n"
            var added = RetouchJob(source: document, target: .photocraft)
            added.status = .running; added.step = "调色"; added.fraction = 0.4
            craft.jobs = [made, added]
            craft.selection = made.id
            shoot(TabbedColumns(studio: craft), size: NSSize(width: 1360, height: 900), to: out.appendingPathComponent("10b-photocraft-done.png"))
            shoot(TabbedColumns(studio: craft), size: NSSize(width: 1360, height: 900), dark: true,
                  to: out.appendingPathComponent("10c-photocraft-done-dark.png"))
            craft.target = .compositor
            shoot(TabbedColumns(studio: craft), size: NSSize(width: 1360, height: 900), to: out.appendingPathComponent("10d-compositor-tab-meanwhile.png"))
        }
    }

    // MARK: disk image window

    /// The installer window's background at 1x and 2x (`<out>.png`, `<out>@2x.png`), for tiffutil to join.
    @MainActor static func dmgBackground(to url: URL) {
        for scale in [1.0, 2.0] {
            let renderer = ImageRenderer(content: DMGBackground())
            renderer.scale = scale
            guard let image = renderer.cgImage else { print("dmg: nothing drawn"); exit(1) }
            let file = scale == 1 ? url : URL(fileURLWithPath: url.deletingPathExtension().path + "@2x.png")
            try? NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: file)
            print("\(file.lastPathComponent)  \(image.width)x\(image.height)")
        }
    }

    /// What Finder should show: the background, the two icons where the disk image puts them, and their names in
    /// black (light mode) or white (dark mode), since Finder draws the names in the system's label color.
    @MainActor static func dmgPreview(background: URL, to url: URL) {
        let app = NSImage(contentsOf: URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../Resources/AppIcon.png")) ?? NSImage()
        let folder = NSWorkspace.shared.icon(forFile: "/Applications")
        let backdrop = NSImage(contentsOf: background) ?? NSImage()
        let view = HStack(spacing: 24) {
            ForEach([false, true], id: \.self) { dark in
                ZStack {
                    Image(nsImage: backdrop).resizable().frame(width: 640, height: 400)
                    ForEach(Array(zip([DMGLayout.app, DMGLayout.folder], [(app, "Compositor 工具"), (folder, "应用程序")])), id: \.0.x) { point, item in
                        VStack(spacing: 4) {
                            Image(nsImage: item.0).resizable().frame(width: DMGLayout.icon, height: DMGLayout.icon)
                            Text(item.1).font(.system(size: DMGLayout.text)).foregroundStyle(dark ? .white : .black)
                        }
                        .position(x: point.x, y: point.y + 12)
                    }
                }
                .frame(width: 640, height: 400)
            }
        }
        .padding(12).background(.gray)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        guard let image = renderer.cgImage else { print("preview: nothing drawn"); exit(1) }
        try? NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: url)
        print("\(url.lastPathComponent)  \(image.width)x\(image.height)")
    }

    // MARK: icon

    @MainActor static func icon(to url: URL) {
        let view = AppIcon().frame(width: 1024, height: 1024)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        guard let image = renderer.cgImage else { print("icon: nothing drawn"); exit(1) }
        let rep = NSBitmapImageRep(cgImage: image)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
        print("\(url.lastPathComponent)  \(image.width)x\(image.height)")
    }
}

/// The window's three columns side by side, for review: offscreen, the split view's glass sidebar and inspector
/// come out blank, so here each column is drawn on its own over a material.
struct Columns: View {
    let studio: Studio
    var body: some View {
        HStack(spacing: 0) {
            SidebarView(studio: studio, addFiles: {}).frame(width: 260).background(.regularMaterial)
            Divider()
            DetailView(studio: studio, addFiles: {})
            Divider()
            InspectorView(studio: studio).frame(width: 330).background(.regularMaterial)
        }
    }
}

/// Columns under the toolbar's tab switch, which offscreen windows don't draw.
struct TabbedColumns: View {
    @Bindable var studio: Studio
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                Picker("修给", selection: $studio.target) {
                    ForEach(Target.allCases) { target in Text(target.title).tag(target) }
                }
                .pickerStyle(.segmented).labelsHidden().frame(width: 230)
                Spacer()
            }
            .padding(.vertical, 8)
            .background(.bar)
            Divider()
            Columns(studio: studio)
        }
    }
}

/// The app icon, on Apple's 1024 grid: the app's mark as an 824-point squircle with the standard drop shadow.
struct AppIcon: View {
    var body: some View {
        AppMark(size: 824, shadow: false)
            .shadow(color: .black.opacity(0.3), radius: 20, y: 12)
            .frame(width: 1024, height: 1024)
    }
}

/// Where the disk image window puts things, in points; build.sh passes the same numbers to dmgbuild.
enum DMGLayout {
    static let size = CGSize(width: 640, height: 400)
    static let app = CGPoint(x: 170, y: 200)
    static let folder = CGPoint(x: 470, y: 200)
    static let icon: CGFloat = 128
    static let text: CGFloat = 13
}

/// The installer window: a photographer's 18% gray card. Finder writes the icon names over it in black or white
/// depending on the appearance, and middle gray is where both read equally well. A frosted tile under each icon, an
/// arrow from the app to the Applications folder, and a line on what to do.
struct DMGBackground: View {
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(white: 0.50), Color(white: 0.44)], startPoint: .top, endPoint: .bottom)
            RadialGradient(colors: [.white.opacity(0.10), .clear], center: UnitPoint(x: 0.5, y: -0.15), startRadius: 0, endRadius: 420)
            ForEach([DMGLayout.app, DMGLayout.folder], id: \.x) { point in
                RoundedRectangle(cornerRadius: 32, style: .continuous)
                    .fill(.white.opacity(0.10))
                    .overlay(RoundedRectangle(cornerRadius: 32, style: .continuous)
                        .strokeBorder(LinearGradient(colors: [.white.opacity(0.35), .white.opacity(0.06)], startPoint: .top, endPoint: .bottom),
                                      lineWidth: 1))
                    .shadow(color: .black.opacity(0.10), radius: 12, y: 6)
                    .frame(width: 172, height: 150)
                    .position(x: point.x, y: point.y - 9)  // ends just under the icon: the name sits on the gray
            }
            DMGArrow()
                .frame(width: 112, height: 28)
                .position(x: (DMGLayout.app.x + DMGLayout.folder.x) / 2, y: DMGLayout.app.y)
            VStack(spacing: 7) {
                Text("Compositor 工具").font(.system(size: 26, weight: .semibold))
                Text("把左边的图标拖到「应用程序」，就装好了").font(.system(size: 13, weight: .medium)).opacity(0.88)
            }
            .foregroundStyle(.white)
            .shadow(color: .black.opacity(0.15), radius: 4, y: 1)
            .position(x: DMGLayout.size.width / 2, y: 62)
            Text("第一次打开时会联网准备运行环境，之后离线使用")
                .font(.system(size: 11)).foregroundStyle(.white.opacity(0.72))
                .position(x: DMGLayout.size.width / 2, y: 338)
        }
        .frame(width: DMGLayout.size.width, height: DMGLayout.size.height)
    }
}

/// A soft dotted arrow pointing right.
struct DMGArrow: View {
    var body: some View {
        HStack(spacing: 7) {
            ForEach(0..<5) { index in
                Circle().fill(.white.opacity(0.30 + 0.12 * Double(index))).frame(width: 7, height: 7)
            }
            Image(systemName: "chevron.right").font(.system(size: 22, weight: .bold)).foregroundStyle(Theme.accentColor)
        }
        .shadow(color: .black.opacity(0.15), radius: 3, y: 1)
    }
}
