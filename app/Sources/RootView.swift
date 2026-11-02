import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// The window: the setup screen until the Python environment is ready, then queue | preview | settings.
struct RootView: View {
    @Bindable var studio: Studio
    @State private var dropping = false

    var body: some View {
        Group {
            if studio.environment.state == .ready {
                main
            } else {
                SetupView(environment: studio.environment)
            }
        }
        .frame(minWidth: 1080, minHeight: 700)
        .tint(Theme.accentColor)
        .background(MainWindowFinder())
    }

    private var main: some View {
        NavigationSplitView {
            SidebarView(studio: studio, addFiles: chooseFiles)
                .navigationSplitViewColumnWidth(min: 230, ideal: 260, max: 340)
        } detail: {
            DetailView(studio: studio, addFiles: chooseFiles)
                .inspector(isPresented: $studio.showInspector) {
                    InspectorView(studio: studio)
                        .inspectorColumnWidth(min: 300, ideal: 330, max: 400)
                }
        }
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button(action: chooseFiles) { Label("添加", systemImage: "plus") }
                    .help("添加照片或 .comp 工程（⌘O）")
            }
            ToolbarItem(placement: .principal) {
                if studio.isRunning {
                    HStack(spacing: 8) {
                        ProgressBar(fraction: studio.progress, height: 5).frame(width: 140)
                        Text("\(studio.batchFinished) / \(studio.batch.count)").font(.system(size: 11, weight: .medium)).monospacedDigit()
                    }
                }
            }
            ToolbarItem(placement: .primaryAction) {
                Button { FloatingMode.shared.enter(studio) } label: { Label("悬浮球", systemImage: "circle.circle") }
                    .help("缩成浮在 Compositor 上的小圆球：点它选功能，一键精修 Compositor 正在编辑的工程")
            }
            ToolbarItem(placement: .primaryAction) {
                Button { studio.showInspector.toggle() } label: { Label("设置", systemImage: "sidebar.trailing") }
                    .help("显示或隐藏精修设置")
            }
        }
        .navigationTitle("Compositor 工具")
        .navigationSubtitle(subtitle)
        .dropDestination(for: URL.self) { urls, _ in
            studio.add(urls)
            return true
        } isTargeted: { dropping = $0 }
        .overlay {
            if dropping {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Theme.accentColor, lineWidth: 3)
                    .background(Theme.accentColor.opacity(0.06), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .padding(8)
                    .allowsHitTesting(false)
            }
        }
    }

    private var subtitle: String {
        if studio.jobs.isEmpty { return "拖入照片开始" }
        let done = studio.jobs.filter { $0.status == .done }.count
        return "\(studio.jobs.count) 张 · 完成 \(done)"
    }

    func chooseFiles() { FilePicker.choose(into: studio) }
}

/// The open panel for photos and projects (the toolbar's +, the sidebar, the empty view and ⌘O).
enum FilePicker {
    static var types: [UTType] { [.jpeg, .png, .tiff, .heic, .heif] + [UTType("com.compositor.project")].compactMap { $0 } }

    @MainActor static func choose(into studio: Studio) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.treatsFilePackagesAsDirectories = false
        panel.allowedContentTypes = types
        panel.prompt = "加入队列"
        panel.message = "选择照片（JPEG、PNG、TIFF、HEIC）或已有的 .comp 工程，可以多选"
        if panel.runModal() == .OK { studio.add(panel.urls) }
    }
}
