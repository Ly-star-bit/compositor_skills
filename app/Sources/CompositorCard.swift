import AppKit
import SwiftUI

/// The sidebar's top: what Compositor is editing, with the button that retouches it there and then; otherwise what
/// it takes to get there (open a project, save the photo as one, allow reading its windows) and its recent projects.
struct CompositorCard: View {
    @Bindable var studio: Studio
    private var link: CompositorLink { studio.compositor }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard(corner: 16, padding: 12)
        .animation(.snappy(duration: 0.25), value: link.front)
    }

    private var header: some View {
        HStack(spacing: 7) {
            CompositorIcon(size: 18)
            Text("Compositor").font(.system(size: 12.5, weight: .semibold))
            Spacer()
            Button { FloatingMode.shared.enter(studio) } label: {
                Image(systemName: "circle.circle").font(.system(size: 12, weight: .semibold))
            }
            .buttonStyle(.plain).foregroundStyle(.secondary)
            .help("缩成悬浮球，浮在 Compositor 上")
            let running = ![.notRunning, .notInstalled].contains(link.front)
            HStack(spacing: 4) {
                Circle().fill(running ? Theme.success : Color.secondary.opacity(0.5)).frame(width: 6, height: 6)
                Text(running ? "运行中" : link.front == .notInstalled ? "未安装" : "未打开")
            }
            .font(.system(size: 10.5)).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var content: some View {
        switch link.front {
        case .project(let url):
            ProjectLine(url: url, caption: "正在编辑", size: 44)
            RetouchButton(studio: studio, project: url, prominent: true)
            if !link.trusted { AccessNote(link: link) }
            AutoSaveToggle(studio: studio)
            AutoQueueToggle(studio: studio)
        case .untitled:
            note("doc.badge.ellipsis", "现在是还没保存的新文档", "在 Compositor 里按 ⌘S 存成 .comp 工程，这里会自动认出它")
        case .unlocated(let title):
            note("questionmark.folder", "Compositor 正在编辑「\(title)」，但没找到它的 .comp 文件",
                 "在 Compositor 里 ⌘S 保存一次，或重新打开这个工程，这里就能认出；也可以在下面的最近工程里点它")
            recentList
        case .noAccess:
            note("lock.open", "让这里自动认出正在编辑的工程", "需要一次「辅助功能」权限，只用来读取 Compositor 窗口里的工程路径")
            Button { link.requestAccess() } label: {
                Label("允许读取", systemImage: "checkmark.shield").frame(maxWidth: .infinity)
            }
            .buttonStyle(.glass).controlSize(.small)
            recentList
        case .noWindow:
            note("macwindow", "Compositor 没有打开的工程", "打开一个工程，这里会自动出现")
            recentList
        case .notRunning, .notInstalled:
            if link.recent.isEmpty {
                note("square.stack.3d.up", link.front == .notInstalled ? "这台 Mac 上没有找到 Compositor" : "在 Compositor 里打开工程",
                     "正在编辑的工程会自动出现在这里，点一下就能精修")
            }
            recentList
        }
    }

    private func note(_ symbol: String, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: symbol).font(.system(size: 13)).foregroundStyle(.secondary).frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 12, weight: .medium))
                Text(detail).font(.system(size: 10.5)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private var recentList: some View {
        let recent = link.otherRecent.prefix(3)
        if !recent.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("最近的工程").font(.system(size: 10.5, weight: .medium)).foregroundStyle(.secondary)
                ForEach(Array(recent), id: \.self) { url in
                    HStack(spacing: 8) {
                        ProjectLine(url: url, caption: nil, size: 28)
                        RetouchButton(studio: studio, project: url, prominent: false)
                    }
                }
            }
        }
    }
}

/// A project's photo and name.
struct ProjectLine: View {
    let url: URL
    let caption: String?
    let size: CGFloat
    var body: some View {
        HStack(spacing: 9) {
            DiskImage(url: url, maxPixels: Int(size * 3))
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: size * 0.2, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: size * 0.2, style: .continuous).strokeBorder(.primary.opacity(0.08), lineWidth: 0.5))
            VStack(alignment: .leading, spacing: 2) {
                if let caption { Text(caption).font(.system(size: 10.5)).foregroundStyle(.secondary) }
                Text(url.deletingPathExtension().lastPathComponent)
                    .font(.system(size: caption == nil ? 11.5 : 13, weight: caption == nil ? .regular : .semibold))
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .help(url.path)
    }
}

/// 精修: runs the current settings on a project right away. Says why not when the settings can't apply to a project.
struct RetouchButton: View {
    @Bindable var studio: Studio
    let project: URL
    let prominent: Bool
    var centered = false
    var adjustable = true   // offer 调整 (opens the settings beside the main window)

    private var job: RetouchJob? { studio.jobs.first { CompositorLink.key($0.source) == CompositorLink.key(project) } }
    private var problem: String? {
        if studio.environment.state != .ready { return "运行环境还没准备好" }
        return Arguments.problem(with: studio.settings, for: RetouchJob(source: project))
    }

    var body: some View {
        let running = job?.status == .running
        let waiting = job?.status == .waiting
        let refreshing = job?.refresh == true
        if prominent {
            VStack(alignment: centered ? .center : .leading, spacing: 6) {
                Button { studio.retouchNow(project) } label: {
                    Label(running ? (refreshing ? "正在重算…" : "正在精修…")
                          : waiting ? "排队中 · 第 \(job.flatMap { studio.place(of: $0.id) } ?? 1) 个"
                          : studio.isRunning ? "加入队列" : "精修这个工程",
                          systemImage: studio.isRunning && !running ? "text.badge.plus" : "paintbrush.pointed.fill")
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(problem != nil || running || waiting)
                if let note = studio.smoothingNote(for: project) {
                    Text(note).font(.system(size: 10.5))
                        .foregroundStyle(note.hasPrefix("磨皮方式已按") ? AnyShapeStyle(.secondary) : AnyShapeStyle(Theme.accentColor))
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: 6) {
                    Text(Arguments.summary(studio.settings)).lineLimit(1).truncationMode(.tail)
                    if adjustable {
                        Button("调整") { studio.showInspector = true }
                            .buttonStyle(.plain).foregroundStyle(Theme.accentColor).fontWeight(.medium)
                            .help("在右边选择要做哪些修饰")
                    }
                }
                .font(.system(size: 11))
                if !running && !waiting && Arguments.canRefresh(project) {  // no file check while it runs
                    HStack(spacing: 7) {
                        Button { studio.refreshNow(project) } label: {
                            Label("重算自动层", systemImage: "arrow.triangle.2.circlepath")
                        }
                        .buttonStyle(.glass).controlSize(.small)
                        .disabled(studio.environment.state != .ready)
                        .help("手修过下面的图层（比如高低频）后，按工程现在的样子重新计算磨皮、眼神光这两层，"
                              + "不加新图层，蒙版和不透明度保留。Compositor 只读得到保存过的内容：先在那里 ⌘S 保存")
                        Text("手修后先 ⌘S 保存").font(.system(size: 10.5)).foregroundStyle(.secondary)
                    }
                }
                Text(problem ?? (job?.status == .done ? (refreshing ? "已重算，Compositor 会自动载入" : "已经修过一次：再点会再加一组图层")
                                 : studio.isRunning && !running && !waiting ? "前面还有 \(studio.pendingCount) 张，会按顺序处理；可以接着切到下一张"
                                 : "完成后 Compositor 会自动载入新图层"))
                    .font(.system(size: 10.5)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(centered ? .center : .leading)
            }
        } else {
            Button(running ? "修图中" : waiting ? "排队中" : studio.isRunning ? "入队" : "精修") { studio.retouchNow(project) }
                .buttonStyle(.glass).controlSize(.small)
                .disabled(problem != nil || running || waiting)
                .help(problem ?? "用右边的设置精修这个工程")
        }
    }
}

/// Under a project found without permission to read Compositor's windows (from the files it holds open): what the
/// permission adds, and the button for it.
struct AccessNote: View {
    let link: CompositorLink
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "lock.open").font(.system(size: 12)).foregroundStyle(.secondary).frame(width: 18)
            Text("认出它靠的是 Compositor 打开着的文件。允许读取它的窗口后，才能跟着标签页切换，精修前也才能替你保存")
                .font(.system(size: 10.5)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Button("允许读取") { link.requestAccess() }.buttonStyle(.glass).controlSize(.mini)
        }
    }
}

/// Compositor's own icon when it is installed, a stand-in otherwise.
struct CompositorIcon: View {
    let size: CGFloat
    var body: some View {
        if let app = Compositor.app {
            Image(nsImage: NSWorkspace.shared.icon(forFile: app.path)).resizable().frame(width: size, height: size)
        } else {
            Image(systemName: "square.stack.3d.up.fill").font(.system(size: size * 0.7)).foregroundStyle(.secondary)
                .frame(width: size, height: size)
        }
    }
}

/// The middle, with nothing queued and a project open in Compositor: that project, large, and the button.
struct CompositorHero: View {
    @Bindable var studio: Studio
    let project: URL
    var body: some View {
        VStack(spacing: 18) {
            DiskImage(url: project, maxPixels: 1200, fill: false)
                .frame(maxWidth: 520, maxHeight: 360)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .shadow(color: .black.opacity(0.18), radius: 18, y: 8)
            VStack(spacing: 6) {
                HStack(spacing: 6) {
                    CompositorIcon(size: 16)
                    Text("Compositor 正在编辑").font(.system(size: 12.5)).foregroundStyle(.secondary)
                }
                Text(project.deletingPathExtension().lastPathComponent).font(.system(size: 24, weight: .semibold))
            }
            RetouchButton(studio: studio, project: project, prominent: true, centered: true).frame(width: 260)
        }
        .padding(32)
    }
}

/// 切换图片时自动加入队列: each new photo brought to the front in Compositor joins the queue by itself.
struct AutoQueueToggle: View {
    @Bindable var studio: Studio
    var body: some View {
        Toggle(isOn: $studio.autoQueue) {
            VStack(alignment: .leading, spacing: 1) {
                Text("切换图片时自动加入队列").font(.system(size: 11.5, weight: .medium))
                Text("在 Compositor 里切到没修过的图，就自动排进来").font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .toggleStyle(.switch).controlSize(.mini)
    }
}

struct AutoSaveToggle: View {
    @Bindable var studio: Studio
    var body: some View {
        Toggle(isOn: $studio.autoSave) {
            VStack(alignment: .leading, spacing: 1) {
                Text("精修前先替你在 Compositor 里保存").font(.system(size: 11.5, weight: .medium))
                Text("按一下它的「存储」，不用自己记着 ⌘S").font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .toggleStyle(.switch).controlSize(.mini)
    }
}
