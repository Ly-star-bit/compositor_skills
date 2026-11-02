import AppKit
import SwiftUI

/// The ball itself: the app's aperture on graphite. A dot tells whether Compositor's project was found (green), a
/// ring fills while it is being retouched, and a check shows for a moment when it's done. Drag to move, click for
/// the panel, right-click for the rest.
struct BallView: View {
    @Bindable var studio: Studio
    private var link: CompositorLink { studio.compositor }

    /// The job for the project in Compositor, else whatever is running.
    private var job: RetouchJob? {
        if let project = link.currentProject,
           let job = studio.jobs.first(where: { CompositorLink.key($0.source) == CompositorLink.key(project) }) { return job }
        return studio.currentJob
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let running = job?.status == .running
            let justDone = job?.status == .done && (job?.finished.map { context.date.timeIntervalSince($0) < 4 } ?? false)
            ZStack {
                Circle()
                    .fill(LinearGradient(colors: [Color(white: 0.28), Color(white: 0.10)], startPoint: .top, endPoint: .bottom))
                    .overlay(Circle().strokeBorder(LinearGradient(colors: [.white.opacity(0.28), .white.opacity(0.05)],
                                                                  startPoint: .top, endPoint: .bottom), lineWidth: 1))
                    .shadow(color: .black.opacity(0.35), radius: 8, y: 4)
                Image(systemName: justDone ? "checkmark" : "camera.aperture")
                    .font(.system(size: justDone ? 24 : 28, weight: justDone ? .bold : .light))
                    .foregroundStyle(justDone ? Theme.success : Color(white: 0.93))
                    .contentTransition(.symbolEffect(.replace))
                if running {
                    Circle().stroke(Theme.accentColor.opacity(0.25), lineWidth: 3).padding(-5)
                    Circle().trim(from: 0, to: max(0.04, job?.fraction ?? 0))
                        .stroke(Theme.accentColor, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .rotationEffect(.degrees(-90)).padding(-5)
                        .animation(.smooth, value: job?.fraction)
                }
                Circle().fill(dotColor(running: running))
                    .frame(width: 11, height: 11)
                    .overlay(Circle().strokeBorder(Color(white: 0.12), lineWidth: 2))
                    .offset(x: 22, y: -22)
                if studio.pendingCount > 0 {  // photos still to do
                    Text("\(studio.pendingCount)")
                        .font(.system(size: 10, weight: .bold)).monospacedDigit().foregroundStyle(.white)
                        .padding(.horizontal, 5).frame(minWidth: 17, minHeight: 17)
                        .background(Theme.accentColor, in: Capsule())
                        .overlay(Capsule().strokeBorder(Color(white: 0.12), lineWidth: 1.5))
                        .offset(x: 22, y: 22)
                }
            }
            .frame(width: FloatingMode.ballSize, height: FloatingMode.ballSize)
            .padding(12)
            .contentShape(Circle().inset(by: 12))
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { _ in FloatingMode.shared.drag() }
                .onEnded { _ in FloatingMode.shared.endDrag() })
            .help(tooltip)
            .contextMenu { menu }
        }
    }

    private func dotColor(running: Bool) -> Color {
        if running { return Theme.accentColor }
        return link.currentProject != nil ? Theme.success : Color(white: 0.55)
    }

    private var tooltip: String {
        switch link.front {
        case .project(let url): "Compositor 正在编辑「\(url.deletingPathExtension().lastPathComponent)」· 点一下选择功能"
        case .untitled: "Compositor 里是还没保存的新文档：⌘S 存成工程后就能精修"
        case .unlocated(let title): "Compositor 正在编辑「\(title)」，但没找到它的 .comp 文件：在那里 ⌘S 保存一次或重新打开它"
        case .noAccess: "点一下，允许读取 Compositor 正在编辑的工程"
        default: "Compositor 工具 · 点一下选择功能"
        }
    }

    @ViewBuilder private var menu: some View {
        if let project = link.currentProject {
            Button("精修「\(project.deletingPathExtension().lastPathComponent)」") { studio.retouchNow(project) }
                .disabled(Arguments.problem(with: studio.settings, for: RetouchJob(source: project)) != nil
                          || studio.environment.state != .ready)
            if Arguments.canRefresh(project) {
                Button("重算自动层（手修后先 ⌘S）") { studio.refreshNow(project) }
                    .disabled(studio.environment.state != .ready)
            }
        }
        Button(FloatingMode.shared.isPickerOpen ? "收起面板" : "选择功能…") { FloatingMode.shared.togglePicker() }
        Divider()
        Button("打开完整窗口") { FloatingMode.shared.leave() }
        Button("退出 Compositor 工具") { NSApp.terminate(nil) }
    }
}

/// The panel the ball opens: what Compositor is editing, the presets and every module as chips, how the DR5
/// smoothing is masked, the color grade, the glow and the strength, and the button. Settings are the main window's own.
struct FloatingPicker: View {
    @Bindable var studio: Studio
    private var link: CompositorLink { studio.compositor }

    var body: some View {
        VStack(spacing: 0) {
            header.padding(14)
            Divider().opacity(0.6)
            if !studio.jobs.isEmpty {
                queue.padding(.horizontal, 14).padding(.vertical, 10)
                Divider().opacity(0.6)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    presets
                    ForEach(ModuleGroup.allCases) { group in
                        section(group.title) { chips(group.modules.map { ($0.flag, $0.title, $0.symbol) }) }
                    }
                    section("DR5 磨皮 · 方式与力度") {
                        VStack(spacing: 6) {
                            HStack(spacing: 6) {
                                ForEach(SmoothingMask.allCases) { mask in
                                    Chip(title: mask.title, symbol: mask == .auto ? "person.crop.circle.dashed" : "paintbrush.pointed",
                                         selected: studio.settings.smoothingMask == mask) { studio.settings.smoothingMask = mask }
                                }
                            }
                            HStack(spacing: 6) {
                                ForEach(SkinStrength.allCases) { strength in
                                    Chip(title: strength.title, selected: studio.settings.skinStrength == strength) {
                                        studio.settings.skinStrength = strength
                                    }
                                }
                            }
                        }
                        .disabled(!studio.settings.isOn("delicious-skin"))
                        .opacity(studio.settings.isOn("delicious-skin") ? 1 : 0.45)
                    }
                    section("质感") {
                        chips([("grain", "胶片颗粒", "circle.dotted")])
                    }
                    looks
                    glow
                    section("辉光与调色强度  \(String(format: "%.1f×", studio.settings.strength))") {
                        Slider(value: Binding(get: { studio.settings.strength },
                                              set: { studio.settings.strength = ($0 * 10).rounded() / 10 }), in: 0.3...1.7)
                            .controlSize(.small)
                    }
                }
                .padding(14)
            }
            .scrollIndicators(.never)
            Divider().opacity(0.6)
            footer.padding(14)
        }
        .frame(width: FloatingMode.pickerSize.width - 12, height: FloatingMode.pickerSize.height - 12)
        .glassCard(corner: 24, padding: 0)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: .black.opacity(0.25), radius: 10, y: 4)
        .padding(6)
        .tint(Theme.accentColor)
    }

    // MARK: parts

    /// The queue: every photo sent from here or the main window, with how it is getting on.
    private var queue: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(studio.pendingCount > 0 ? "队列 · 还剩 \(studio.pendingCount) 张" : "队列 · 都处理完了")
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                Spacer()
                if studio.finishedCount > 0 && !studio.isRunning {
                    Button("清除已完成") { studio.clearFinished() }
                        .buttonStyle(.plain).font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.accentColor)
                }
            }
            ScrollView {
                VStack(spacing: 4) {
                    ForEach(studio.jobs) { job in QueueRow(studio: studio, job: job) }
                }
            }
            .frame(maxHeight: min(CGFloat(studio.jobs.count) * 34, 136))
            .scrollIndicators(.never)
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            if let project = link.currentProject {
                ProjectLine(url: project, caption: "Compositor 正在编辑", size: 40)
            } else {
                AppMark(size: 34, shadow: false)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Compositor 工具").font(.system(size: 13, weight: .semibold))
                    Text(stateLine).font(.system(size: 10.5)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            iconButton("arrow.up.left.and.arrow.down.right", "打开完整窗口") { FloatingMode.shared.leave() }
            iconButton("xmark", "收起") { FloatingMode.shared.closePicker() }
        }
    }

    private var stateLine: String {
        switch link.front {
        case .untitled: "Compositor 里是还没保存的新文档，⌘S 存成 .comp 工程后会自动出现"
        case .unlocated(let title): "Compositor 正在编辑「\(title)」，但没找到它的 .comp 文件：在那里 ⌘S 保存一次或重新打开它"
        case .noAccess: "允许读取后，会自动认出 Compositor 正在编辑的工程"
        case .noWindow: "Compositor 没有打开的工程"
        case .notRunning: "打开 Compositor 里的工程，会自动出现在这里"
        case .notInstalled: "这台 Mac 上没有找到 Compositor"
        case .project: ""
        }
    }

    private func iconButton(_ symbol: String, _ help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                .frame(width: 24, height: 24).background(.primary.opacity(0.06), in: Circle())
        }
        .buttonStyle(.plain).help(help)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
            content()
        }
    }

    private var presets: some View {
        section("预设") {
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 3), spacing: 6) {
                ForEach(Preset.allCases) { preset in
                    Chip(title: preset.title, selected: studio.settings.preset == preset) { studio.settings.choose(preset) }
                }
            }
            if studio.settings.isCustomized {
                HStack {
                    Text("已在「\(studio.settings.preset.title)」基础上调整").font(.system(size: 10.5)).foregroundStyle(.secondary)
                    Spacer()
                    Button("恢复") { studio.settings.choose(studio.settings.preset) }
                        .buttonStyle(.plain).font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.accentColor)
                }
            }
        }
    }

    private func chips(_ items: [(flag: String, title: String, symbol: String)]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 2), spacing: 6) {
            ForEach(items, id: \.flag) { item in
                ModuleChip(title: item.title, symbol: item.symbol, isOn: studio.settings.isOn(item.flag)) {
                    studio.settings.set(item.flag, !studio.settings.isOn(item.flag))
                }
            }
        }
    }

    private var looks: some View {
        section("调色") {
            HStack(spacing: 4) {
                ForEach(Look.allCases) { look in
                    Button { studio.settings.look = look } label: {
                        VStack(spacing: 4) {
                            ZStack {
                                Circle().fill(LinearGradient(colors: look.swatch.map { Color(.sRGB, red: $0.0, green: $0.1, blue: $0.2) },
                                                             startPoint: .topLeading, endPoint: .bottomTrailing))
                                if look == .none { Image(systemName: "nosign").font(.system(size: 10)).foregroundStyle(.secondary) }
                            }
                            .frame(width: 26, height: 26)
                            .overlay(Circle().strokeBorder(studio.settings.look == look ? AnyShapeStyle(Theme.ink) : AnyShapeStyle(.primary.opacity(0.1)),
                                                           lineWidth: studio.settings.look == look ? 2 : 0.5).padding(-3))
                            Text(look.title).font(.system(size: 9.5, weight: studio.settings.look == look ? .semibold : .regular))
                                .lineLimit(1).minimumScaleFactor(0.8)
                        }
                        .frame(maxWidth: .infinity)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var glow: some View {
        section("辉光") {
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 4), spacing: 6) {
                ForEach(GlowStyle.allCases) { style in
                    GlowTile(style: style, selected: studio.settings.glow == style) { studio.settings.glow = style }
                }
            }
        }
    }

    @ViewBuilder private var footer: some View {
        if let project = link.currentProject {
            let job = studio.jobs.first { CompositorLink.key($0.source) == CompositorLink.key(project) }
            VStack(alignment: .leading, spacing: 8) {
                if job?.status == .running {
                    HStack(spacing: 8) {
                        ProgressRing(fraction: job?.fraction ?? 0).frame(width: 16, height: 16)
                        Text(job?.step ?? "处理中…").font(.system(size: 12, weight: .semibold))
                        Spacer()
                        Button("停止") { studio.stop() }.buttonStyle(.plain).font(.system(size: 11.5)).foregroundStyle(.secondary)
                    }
                    ProgressBar(fraction: job?.fraction ?? 0)
                } else {
                    if let job, case .failed(let reason) = job.status {
                        Label(reason, systemImage: "xmark.octagon.fill").font(.system(size: 11)).foregroundStyle(Theme.failure)
                            .lineLimit(2)
                    } else if let job, job.status == .done {
                        Label(String(format: job.refresh ? "重算完成 · %.1f 秒，Compositor 会自动载入"
                                                         : "完成 · %.1f 秒，Compositor 会自动载入新图层", job.seconds ?? 0),
                              systemImage: "checkmark.circle.fill")
                            .font(.system(size: 11)).foregroundStyle(Theme.success)
                    }
                    RetouchButton(studio: studio, project: project, prominent: true, adjustable: false)
                }
                if !link.trusted { AccessNote(link: link) }
                AutoSaveToggle(studio: studio)
                AutoQueueToggle(studio: studio)
            }
        } else if case .noAccess = link.front {
            Button { link.requestAccess() } label: {
                Label("允许读取 Compositor 正在编辑的工程", systemImage: "checkmark.shield").frame(maxWidth: .infinity)
            }
            .buttonStyle(PrimaryButtonStyle())
        } else if let recent = link.otherRecent.first {
            VStack(alignment: .leading, spacing: 6) {
                Text("最近的工程").font(.system(size: 10.5, weight: .medium)).foregroundStyle(.secondary)
                HStack(spacing: 8) {
                    ProjectLine(url: recent, caption: nil, size: 30)
                    RetouchButton(studio: studio, project: recent, prominent: false)
                }
            }
        } else {
            Text("在 Compositor 里打开一个 .comp 工程，这里就能一键精修")
                .font(.system(size: 11)).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A module as a chip: its icon and name, ink when on.
struct ModuleChip: View {
    let title: String
    let symbol: String
    let isOn: Bool
    var action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: symbol).font(.system(size: 11, weight: .semibold)).frame(width: 14)
                Text(title).font(.system(size: 11.5, weight: isOn ? .semibold : .regular)).lineLimit(1).minimumScaleFactor(0.85)
                Spacer(minLength: 0)
                if isOn { Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)) }
            }
            .padding(.horizontal, 10).frame(height: 30)
            .foregroundStyle(isOn ? Theme.onInk : .primary)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(isOn ? AnyShapeStyle(Theme.ink) : AnyShapeStyle(.primary.opacity(0.06))))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .animation(.snappy(duration: 0.15), value: isOn)
    }
}

/// One photo in the floating panel's queue: its picture and name, and a ring, 排队中, a check or a cross.
struct QueueRow: View {
    @Bindable var studio: Studio
    let job: RetouchJob
    var body: some View {
        HStack(spacing: 8) {
            DiskImage(url: job.render ?? job.source, maxPixels: 90)
                .frame(width: 28, height: 28)
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(job.name).font(.system(size: 11.5, weight: .medium)).lineLimit(1).truncationMode(.middle)
                Text(detail).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 4)
            status
        }
        .frame(height: 30)
        .help(job.source.path)
    }

    private var detail: String {
        switch job.status {
        case .running: job.step.isEmpty ? "处理中…" : job.step
        case .waiting: "排队中 · 第 \(studio.place(of: job.id) ?? 1) 个"
        case .done: String(format: job.refresh ? "重算完成 · %.1f 秒" : "完成 · %.1f 秒", job.seconds ?? 0)
        case .failed(let reason): reason
        case .cancelled: "已取消"
        }
    }

    @ViewBuilder private var status: some View {
        switch job.status {
        case .running: ProgressRing(fraction: job.fraction ?? 0).frame(width: 16, height: 16)
        case .waiting: Image(systemName: "clock").font(.system(size: 12)).foregroundStyle(.secondary)
        case .done: Image(systemName: "checkmark.circle.fill").font(.system(size: 14)).foregroundStyle(Theme.success)
        case .failed: Image(systemName: "xmark.circle.fill").font(.system(size: 14)).foregroundStyle(Theme.failure)
        case .cancelled: Image(systemName: "stop.circle").font(.system(size: 14)).foregroundStyle(.secondary)
        }
    }
}
