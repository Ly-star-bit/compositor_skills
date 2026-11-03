import AppKit
import SwiftUI

/// The sidebar's top on the PhotoCraft tab: where the documents go and how they come back (PhotoCraft doesn't watch
/// its files: after layers are added, File › Revert reloads one that is open there, unless PhotoCraft runs linked and
/// the script does it), whether PhotoCraft's engine is on this Mac yet, and the documents made here, each with 重算 for
/// after manual work in PhotoCraft.
struct PhotoCraftCard: View {
    @Bindable var studio: Studio
    /// PhotoCraft listens on the link's port: started linked (checked every few seconds while the card shows).
    @State private var linked = false
    @State private var running = false
    @State private var startFailed = false

    /// Documents made or added to in this session, newest first.
    private var documents: [URL] {
        let done: [RetouchJob] = studio.jobs.filter { $0.target == .photocraft && $0.status == .done }
        let newest: [RetouchJob] = done.sorted { ($0.finished ?? .distantPast) > ($1.finished ?? .distantPast) }
        var seen = Set<String>()
        var out: [URL] = []
        for job in newest {
            if let project = job.project, seen.insert(CompositorLink.key(project)).inserted { out.append(project) }
        }
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 7) {
                PhotoCraftIcon(size: 18)
                Text("PhotoCraft").font(.system(size: 12.5, weight: .semibold))
                Spacer()
                let ready = PhotoCraftApp.cli != nil
                HStack(spacing: 4) {
                    Circle().fill(ready ? Theme.success : Theme.warning).frame(width: 6, height: 6)
                    Text(ready ? "引擎就绪" : "首次使用时下载")
                }
                .font(.system(size: 10.5)).foregroundStyle(.secondary)
                .help(ready ? "photocraft-cli（PhotoCraft 的命令行引擎）已在这台 Mac 上，修图全程没有窗口"
                            : "第一次修 PhotoCraft 文档时，自动下载 photocraft-cli \(PhotoCraftApp.cliVersion)（约 107 MB），按 SHA-256 和 Apple 公证校验后缓存")
            }
            note("doc.badge.plus", "照片新建 .pcraft 文档", "放在照片旁边；拖入已有的 .pcraft 会追加图层，同样的模块不会加两遍")
            if linked {
                note("link", "已联动 PhotoCraft",
                     "在 PhotoCraft 里开着的文档：精修前替你保存，做完自动重新载入，不用再按 ⌘S 和 F12（重新载入后它的历史记录从头开始）")
            } else {
                let installed = PhotoCraftApp.app(toOpen: URL(fileURLWithPath: "/document.pcraft")) != nil
                note("arrow.uturn.backward.circle", "PhotoCraft 不会自动刷新",
                     "追加或重算前先在 PhotoCraft 里 ⌘S；做完在 PhotoCraft 里按 F12（文件 › 恢复）载入，先别按 ⌘S"
                     + (installed ? "。用下面的按钮联动启动 PhotoCraft，这两步就自动了" : ""))
                if installed { linkButton }
            }
            if !documents.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("这次修过的文档").font(.system(size: 10.5, weight: .medium)).foregroundStyle(.secondary)
                    ForEach(documents.prefix(3), id: \.self) { document in
                        HStack(spacing: 9) {
                            DiskImage(url: Arguments.renderImage(of: document), maxPixels: 84)  // a .pcraft has no preview of its own here
                                .frame(width: 28, height: 28)
                                .clipShape(RoundedRectangle(cornerRadius: 5.6, style: .continuous))
                                .overlay(RoundedRectangle(cornerRadius: 5.6, style: .continuous).strokeBorder(.primary.opacity(0.08), lineWidth: 0.5))
                            Text(document.deletingPathExtension().lastPathComponent)
                                .font(.system(size: 11.5)).lineLimit(1).truncationMode(.middle)
                                .help(document.path)
                            Spacer(minLength: 0)
                            RefreshDocumentButton(studio: studio, document: document)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard(corner: 16, padding: 12)
        .task {
            while !Task.isCancelled {
                linked = await Task.detached { PhotoCraftApp.isListening() }.value
                running = PhotoCraftApp.isRunning
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    /// Starts PhotoCraft linked: only when it isn't running (a running PhotoCraft keeps how it was started).
    private var linkButton: some View {
        HStack(spacing: 8) {
            Button {
                startFailed = (try? PhotoCraftApp.startLinked()) == nil
            } label: {
                Label("联动启动 PhotoCraft", systemImage: "link")
            }
            .buttonStyle(.glass).controlSize(.small)
            .disabled(running)
            .help("用联动方式启动 PhotoCraft：打开它在本机的控制端口（凭只有你能读的令牌连接），并允许它的自动化读写你的"
                  + "用户文件夹。之后在 PhotoCraft 里开着的文档，精修前自动保存、做完自动重新载入")
            if running {
                Text("先退出 PhotoCraft 再点").font(.system(size: 10.5)).foregroundStyle(.secondary)
            } else if startFailed {
                Text("没能启动 PhotoCraft").font(.system(size: 10.5)).foregroundStyle(Theme.warning)
            }
        }
        .padding(.leading, 26)
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
}

/// 重算 for a PhotoCraft document: the smoothing recomputed and sculpt, eye brilliance and soft focus re-stamped from
/// the document as saved, nothing added.
struct RefreshDocumentButton: View {
    @Bindable var studio: Studio
    let document: URL
    var label = "重算"

    private var job: RetouchJob? { studio.jobs.first { CompositorLink.key($0.source) == CompositorLink.key(document) } }

    var body: some View {
        let busy = job?.status == .running || job?.status == .waiting
        Button { studio.refreshNow(document) } label: {
            Label(job?.status == .running ? "处理中" : busy ? "排队中" : label, systemImage: "arrow.triangle.2.circlepath")
        }
        .buttonStyle(.glass).controlSize(.small)
        .disabled(busy || studio.environment.state != .ready)
        .help("在 PhotoCraft 里手修过下面的图层（比如高低频）后点这里（没联动时先在 PhotoCraft 里 ⌘S）。按文档现在的样子重算磨皮，"
              + "重新盖印骨相、眼神光、梦幻柔焦，不加新图层，蒙版和不透明度保留；联动时自动重新载入，没联动时完成后在 PhotoCraft 里按 F12")
    }
}

/// PhotoCraft's own icon when it is installed, a stand-in otherwise.
struct PhotoCraftIcon: View {
    let size: CGFloat
    var body: some View {
        if let app = PhotoCraftApp.app(toOpen: URL(fileURLWithPath: "/document.pcraft")) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: app.path)).resizable().frame(width: size, height: size)
        } else {
            Image(systemName: "camera.aperture").font(.system(size: size * 0.72)).foregroundStyle(.secondary)
                .frame(width: size, height: size)
        }
    }
}
