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
                    .help("添加照片、.comp 工程或 .pcraft 文档（⌘O）")
            }
            ToolbarItem(placement: .principal) {
                HStack(spacing: 14) {
                    Picker("修给", selection: $studio.target) {
                        ForEach(Target.allCases) { target in Text(target.title).tag(target) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 230)
                    .help("修给哪个编辑器：Compositor（.comp 工程）或 PhotoCraft（.pcraft 文档）。右边的设置两边共用")
                    if studio.isRunning {
                        HStack(spacing: 8) {
                            ProgressBar(fraction: studio.progress, height: 5).frame(width: 140)
                            Text("\(studio.batchFinished) / \(studio.batch.count)").font(.system(size: 11, weight: .medium)).monospacedDigit()
                        }
                    }
                }
            }
            if studio.target == .compositor {
                ToolbarItem(placement: .primaryAction) {
                    Button { FloatingMode.shared.enter(studio) } label: { Label("悬浮球", systemImage: "circle.circle") }
                        .help("缩成浮在 Compositor 上的小圆球：点它选功能，一键精修 Compositor 正在编辑的工程")
                }
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
        let jobs = studio.visibleJobs
        if jobs.isEmpty { return "拖入照片开始" }
        let done = jobs.filter { $0.status == .done }.count
        return "\(jobs.count) 张 · 完成 \(done)"
    }

    func chooseFiles() { FilePicker.choose(into: studio) }
}

/// The open panel for photos and projects (the toolbar's +, the sidebar, the empty view and ⌘O).
enum FilePicker {
    static var types: [UTType] {
        [.jpeg, .png, .tiff, .heic, .heif] + [UTType("com.compositor.project"), UTType(filenameExtension: "pcraft")].compactMap { $0 }
    }

    @MainActor static func choose(into studio: Studio) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.treatsFilePackagesAsDirectories = false
        panel.allowedContentTypes = types
        panel.prompt = "加入队列"
        panel.message = studio.target == .compositor
            ? "选择照片（JPEG、PNG、TIFF、HEIC）或已有的 .comp 工程，可以多选"
            : "选择照片（JPEG、PNG、TIFF、HEIC）或已有的 .pcraft 文档，可以多选：照片会新建 PhotoCraft 文档"
        if panel.runModal() == .OK { studio.add(panel.urls) }
    }
}
