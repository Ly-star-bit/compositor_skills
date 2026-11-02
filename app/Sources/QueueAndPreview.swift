import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// The queue: one row per photo or project, with its state.
struct SidebarView: View {
    @Bindable var studio: Studio
    var addFiles: () -> Void

    var body: some View {
        List(selection: $studio.selection) {
            Section {
                ForEach(studio.jobs) { job in
                    JobRow(job: job)
                        .tag(job.id)
                        .contextMenu {
                            if let project = job.project, Compositor.app != nil {
                                Button("在 Compositor 中打开") { Compositor.open([project]) }
                            }
                            if job.status != .running && job.status != .waiting && !studio.isRunning {
                                Button("重新排队") { studio.retry(job.id) }
                            }
                            Button("从队列移除", role: .destructive) { studio.remove(job.id) }
                                .disabled(job.status == .running)
                        }
                }
            } header: {
                HStack {
                    Text(studio.jobs.isEmpty ? "队列" : "队列 · \(studio.jobs.count) 张")
                    Spacer()
                    if studio.finishedCount > 0 && !studio.isRunning {
                        Button("清除已完成") { studio.clearFinished() }
                            .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(Theme.accentColor)
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .overlay {
            if studio.jobs.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "tray").font(.system(size: 26, weight: .light)).foregroundStyle(.tertiary)
                    Text("还没有照片").font(.system(size: 12)).foregroundStyle(.secondary)
                }
            }
        }
        .safeAreaInset(edge: .top) {
            CompositorCard(studio: studio).padding(.horizontal, 10).padding(.top, 4).padding(.bottom, 2)
        }
        .safeAreaInset(edge: .bottom) {
            Button(action: addFiles) {
                Label("添加照片或工程…", systemImage: "plus")
                    .font(.system(size: 12.5, weight: .medium))
                    .frame(maxWidth: .infinity, minHeight: 30)
            }
            .buttonStyle(.glass)
            .padding(12)
        }
        .frame(minWidth: 230)
    }
}

struct JobRow: View {
    let job: RetouchJob
    var body: some View {
        HStack(spacing: 10) {
            DiskImage(url: job.render ?? job.source, maxPixels: 120)
                .frame(width: 44, height: 44)
                .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(.primary.opacity(0.08), lineWidth: 0.5))
                .overlay(alignment: .bottomTrailing) {
                    if job.isProject {
                        Image(systemName: "square.stack.3d.up.fill")
                            .font(.system(size: 8, weight: .bold)).foregroundStyle(.white)
                            .padding(3).background(Theme.accentColor, in: Circle()).offset(x: 3, y: 3)
                    }
                }
            VStack(alignment: .leading, spacing: 3) {
                Text(job.name).font(.system(size: 12.5, weight: .medium)).lineLimit(1).truncationMode(.middle)
                HStack(spacing: 5) {
                    if job.status == .running {  // the ring says it is running; the room goes to what it is doing
                        Text(job.step.isEmpty ? "处理中…" : job.step)
                            .font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.accentColor).lineLimit(1)
                    } else {
                        StatusBadge(status: job.status)
                    }
                    if let seconds = job.seconds, job.status == .done {
                        Text(String(format: "%.1f 秒", seconds)).font(.system(size: 10.5)).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer(minLength: 0)
            if job.status == .running { ProgressRing(fraction: job.fraction ?? 0).frame(width: 18, height: 18) }
        }
        .padding(.vertical, 3)
    }
}

/// The middle: the drop zone with nothing queued, otherwise the selected photo, its result and check images.
struct DetailView: View {
    @Bindable var studio: Studio
    var addFiles: () -> Void
    @State private var tab = "result"
    @State private var showLog = false

    var body: some View {
        ZStack {
            backdrop
            if let job = studio.selectedJob {
                preview(job)
            } else if let project = studio.compositor.currentProject {
                VStack(spacing: 4) {
                    CompositorHero(studio: studio, project: project)
                    Button("也可以拖入照片或工程，批量处理…", action: addFiles)
                        .buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(.secondary)
                }
            } else {
                EmptyDropZone(addFiles: addFiles)
            }
        }
    }

    private var backdrop: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor)
            LinearGradient(colors: [.primary.opacity(0.025), .clear], startPoint: .top, endPoint: .center)
        }
        .ignoresSafeArea()
    }

    private func tabs(for job: RetouchJob) -> [(String, String)] {
        var list: [(String, String)] = []
        if job.render != nil { list.append(("result", "效果")) }
        list.append(("original", "原图"))
        list += job.checks.map { ($0.path, Studio.checkTitle($0)) }
        return list
    }

    private func image(for job: RetouchJob, tab: String) -> URL? {
        switch tab {
        case "result": job.render ?? job.source
        case "original": job.source
        default: URL(fileURLWithPath: tab)
        }
    }

    private func preview(_ job: RetouchJob) -> some View {
        let available = tabs(for: job)
        // without a render (a project open in Compositor shows its result there) the before/after check leads
        let fallback = job.render == nil && !job.checks.isEmpty ? job.checks[0].path : (available.first?.0 ?? "original")
        let current = available.contains { $0.0 == tab } ? tab : fallback
        return VStack(spacing: 14) {
            header(job)
            if available.count > 1 {
                ScrollView(.horizontal) {
                    HStack(spacing: 6) {
                        ForEach(available, id: \.0) { key, title in
                            Chip(title: title, selected: current == key) { tab = key }.fixedSize()
                        }
                    }
                    .padding(.horizontal, 2)
                }
                .scrollIndicators(.never)
            }
            ZStack(alignment: .bottom) {
                DiskImage(url: image(for: job, tab: current), maxPixels: 2200, fill: false)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .shadow(color: .black.opacity(0.18), radius: 18, y: 8)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                if job.status == .running {
                    VStack(alignment: .leading, spacing: 9) {
                        HStack(spacing: 9) {
                            ProgressRing(fraction: job.fraction ?? 0).frame(width: 18, height: 18)
                            Text(job.step.isEmpty ? "处理中…" : job.step).font(.system(size: 13, weight: .semibold))
                            Spacer(minLength: 12)
                            Text("\(Int(((job.fraction ?? 0) * 100).rounded()))%")
                                .font(.system(size: 12, weight: .medium)).monospacedDigit().foregroundStyle(.secondary)
                        }
                        ProgressBar(fraction: job.fraction ?? 0)
                    }
                    .frame(width: 300)
                    .glassCard(corner: 18, padding: 14)
                    .padding(.bottom, 22)
                    .animation(.smooth, value: job.fraction)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            footer(job)
        }
        .padding(22)
        .animation(.snappy(duration: 0.25), value: current)
    }

    private func header(_ job: RetouchJob) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(job.name).font(.system(size: 20, weight: .semibold)).lineLimit(1)
                HStack(spacing: 8) {
                    StatusBadge(status: job.status)
                    Text(job.isProject ? "已有工程 · 追加图层" : (job.project?.lastPathComponent ?? "新建工程"))
                        .font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    if let seconds = job.seconds, job.status.isFinished {
                        Text(String(format: "· %.1f 秒", seconds)).font(.system(size: 11.5)).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            if let project = job.project {
                Button { Compositor.open([project]) } label: {
                    Label("在 Compositor 中打开", systemImage: "arrow.up.forward.app")
                }
                .buttonStyle(.glassProminent)
                .tint(Theme.ink)
                .disabled(Compositor.app == nil)
                .help(Compositor.app == nil ? "这台 Mac 上没有找到 Compositor" : "用 Compositor 打开这个工程")
            }
        }
    }

    @ViewBuilder private func footer(_ job: RetouchJob) -> some View {
        let problem: String? = if case .failed(let reason) = job.status { reason } else { nil }
        if problem != nil || !job.warnings.isEmpty || !job.log.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                if let problem {
                    Label(problem, systemImage: "xmark.octagon.fill").foregroundStyle(Theme.failure)
                        .font(.system(size: 12, weight: .medium))
                }
                ForEach(job.warnings.prefix(3), id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(2)
                        .symbolRenderingMode(.multicolor)
                }
                if !job.log.isEmpty {
                    DisclosureGroup(isExpanded: $showLog) {
                        ScrollView {
                            Text(job.log).font(.system(size: 10.5, design: .monospaced)).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(maxHeight: 160)
                    } label: {
                        Text("运行记录").font(.system(size: 11.5, weight: .medium))
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassCard(corner: 14, padding: 12)
        }
    }
}

/// What the middle shows with nothing queued: a place to drop photos.
struct EmptyDropZone: View {
    var addFiles: () -> Void
    var body: some View {
        VStack(spacing: 22) {
            ZStack {
                Circle().fill(.primary.opacity(0.06)).frame(width: 92, height: 92)
                Image(systemName: "photo.stack").font(.system(size: 36, weight: .regular)).foregroundStyle(.secondary)
            }
            VStack(spacing: 8) {
                Text("把照片拖到这里").font(.system(size: 26, weight: .semibold))
                Text("JPEG、PNG、TIFF、HEIC，或者已有的 .comp 工程 · 可以一次拖入很多张")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
            }
            Button(action: addFiles) {
                Label("选择照片…", systemImage: "plus").frame(width: 160)
            }
            .buttonStyle(PrimaryButtonStyle())
            .frame(width: 200)
            HStack(spacing: 10) {
                ForEach([("circle.grid.2x2", "中性灰 · 双曲线"), ("circle.hexagongrid", "DR5 磨皮 · 可手动刷出"),
                         ("square.3.layers.3d", "生成可编辑图层"), ("rectangle.stack", "批量处理")], id: \.1) { symbol, text in
                    Label(text, systemImage: symbol)
                        .font(.system(size: 11.5, weight: .medium))
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .glassCard(corner: 16, padding: 0)
                }
            }
            .padding(.top, 6)
        }
        .padding(48)
        .frame(maxWidth: 640)
        .background {
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .strokeBorder(.primary.opacity(0.16), style: StrokeStyle(lineWidth: 1.5, dash: [7, 6]))
        }
        .padding(40)
    }
}

/// A small ring that fills as a photo is worked on.
struct ProgressRing: View {
    let fraction: Double
    var body: some View {
        ZStack {
            Circle().stroke(Theme.accentColor.opacity(0.18), lineWidth: 2.5)
            Circle().trim(from: 0, to: max(0.04, fraction))
                .stroke(Theme.accentColor, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                .rotationEffect(.degrees(-90))
        }
        .animation(.smooth, value: fraction)
    }
}
